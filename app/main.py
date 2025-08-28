import os
from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
import boto3
from botocore.exceptions import ClientError

app = FastAPI(title="AWS Network Validator")

# --- Konfigurasi target ---
REGION = os.getenv("AWS_REGION", "ap-southeast-1")
VPC_ID = os.getenv("TARGET_VPC_ID", "")
PUBLIC_SUBNET_ID = os.getenv("PUBLIC_SUBNET_ID", "")
PRIVATE_SUBNET_ID = os.getenv("PRIVATE_SUBNET_ID", "")
WEB_SG_ID = os.getenv("WEB_SG_ID", "")
DB_SG_ID = os.getenv("DB_SG_ID", "")

ec2 = boto3.client("ec2", region_name=REGION)

templates = Jinja2Templates(directory="app/templates")
app.mount("/static", StaticFiles(directory="app/static"), name="static")


def _get_vpc_cidr(vpc_id: str) -> str:
    resp = ec2.describe_vpcs(VpcIds=[vpc_id])
    cidr = resp["Vpcs"][0]["CidrBlock"]
    return cidr


def _get_rt_for_subnet(subnet_id: str) -> dict:
    resp = ec2.describe_route_tables(
        Filters=[{"Name": "association.subnet-id", "Values": [subnet_id]}]
    )
    if resp["RouteTables"]:
        return resp["RouteTables"][0]
    # fallback: cari RT yg ter-associate via main association di VPC yg sama
    subnet = ec2.describe_subnets(SubnetIds=[subnet_id])["Subnets"][0]
    vpc_id = subnet["VpcId"]
    resp2 = ec2.describe_route_tables(
        Filters=[{"Name": "vpc-id", "Values": [vpc_id]}]
    )
    for rt in resp2["RouteTables"]:
        for assoc in rt.get("Associations", []):
            if assoc.get("Main"):
                return rt
    raise RuntimeError("Route Table untuk subnet tidak ditemukan")


def _check_web_sg_allows_http_from_internet(sg_id: str) -> (bool, str):
    sg = ec2.describe_security_groups(GroupIds=[sg_id])["SecurityGroups"][0]
    for perm in sg.get("IpPermissions", []):
        if perm.get("IpProtocol") in ("tcp", "-1"):
            from_port = perm.get("FromPort")
            to_port = perm.get("ToPort")
            if from_port is not None and to_port is not None and from_port <= 80 <= to_port:
                # Cek 0.0.0.0/0
                for ipr in perm.get("IpRanges", []):
                    if ipr.get("CidrIp") == "0.0.0.0/0":
                        return True, "HTTP:80 diizinkan dari Internet"
    return False, "Inbound HTTP:80 dari Internet tidak ditemukan"


def _check_db_sg_allows_3306_from_websg(db_sg_id: str, web_sg_id: str, vpc_cidr: str) -> (bool, str):
    sg = ec2.describe_security_groups(GroupIds=[db_sg_id])["SecurityGroups"][0]
    for perm in sg.get("IpPermissions", []):
        if perm.get("IpProtocol") in ("tcp", "-1"):
            from_port = perm.get("FromPort")
            to_port = perm.get("ToPort")
            if from_port is not None and to_port is not None and from_port <= 3306 <= to_port:
                # 1) SG to SG
                for pair in perm.get("UserIdGroupPairs", []):
                    if pair.get("GroupId") == web_sg_id:
                        return True, "MySQL:3306 diizinkan dari Web SG"
                # 2) Boleh juga dari CIDR VPC (kurang ideal tapi sering dipakai di lab)
                for ipr in perm.get("IpRanges", []):
                    if ipr.get("CidrIp") == vpc_cidr:
                        return True, f"MySQL:3306 diizinkan dari {vpc_cidr}"
    return False, "Inbound 3306 dari Web SG/VPC tidak ditemukan"


def _check_public_rt_has_igw(subnet_id: str) -> (bool, str):
    rt = _get_rt_for_subnet(subnet_id)
    for r in rt.get("Routes", []):
        if r.get("DestinationCidrBlock") == "0.0.0.0/0" and str(r.get("GatewayId", "")).startswith("igw-"):
            return True, "Public RT punya 0.0.0.0/0 → IGW"
    return False, "Route ke Internet Gateway tidak ditemukan pada Public RT"


def _check_private_rt_no_igw(subnet_id: str) -> (bool, str):
    rt = _get_rt_for_subnet(subnet_id)
    for r in rt.get("Routes", []):
        if r.get("DestinationCidrBlock") == "0.0.0.0/0":
            # Boleh NAT, tidak boleh IGW
            if str(r.get("GatewayId", "")).startswith("igw-"):
                return False, "Private RT memiliki route 0.0.0.0/0 langsung ke IGW (tidak boleh)"
            if str(r.get("NatGatewayId", "")).startswith("nat-"):
                return True, "Private RT 0.0.0.0/0 → NAT (benar)"
            # Jika ada target lain (eni/i- nat instance) kita anggap OK
            if r.get("NetworkInterfaceId") or r.get("InstanceId"):
                return True, "Private RT 0.0.0.0/0 melalui instance/ENI (OK jika NAT Instance)"
    # Jika tidak ada route 0.0.0.0/0 sama sekali, itu juga dianggap OK (benar-benar private)
    return True, "Private RT tidak punya route 0.0.0.0/0 (tetap OK)"


@app.get("/")
def index(request: Request):
    # Validasi input env
    required = {
        "TARGET_VPC_ID": VPC_ID,
        "PUBLIC_SUBNET_ID": PUBLIC_SUBNET_ID,
        "PRIVATE_SUBNET_ID": PRIVATE_SUBNET_ID,
        "WEB_SG_ID": WEB_SG_ID,
        "DB_SG_ID": DB_SG_ID,
    }
    missing = [k for k, v in required.items() if not v]
    if missing:
        return {"error": f"Environment variables kurang: {', '.join(missing)}"}

    try:
        vpc_cidr = _get_vpc_cidr(VPC_ID)
        web_ok, web_msg = _check_web_sg_allows_http_from_internet(WEB_SG_ID)
        db_ok, db_msg = _check_db_sg_allows_3306_from_websg(DB_SG_ID, WEB_SG_ID, vpc_cidr)
        pub_ok, pub_msg = _check_public_rt_has_igw(PUBLIC_SUBNET_ID)
        prv_ok, prv_msg = _check_private_rt_no_igw(PRIVATE_SUBNET_ID)

        overall_ok = web_ok and db_ok and pub_ok and prv_ok

        context = {
            "request": request,
            "region": REGION,
            "vpc_id": VPC_ID,
            "vpc_cidr": vpc_cidr,
            "public_subnet": PUBLIC_SUBNET_ID,
            "private_subnet": PRIVATE_SUBNET_ID,
            "web_sg": WEB_SG_ID,
            "db_sg": DB_SG_ID,
            "checks": {
                "web": {"ok": web_ok, "msg": web_msg},
                "db": {"ok": db_ok, "msg": db_msg},
                "pub": {"ok": pub_ok, "msg": pub_msg},
                "prv": {"ok": prv_ok, "msg": prv_msg},
            },
            "overall_ok": overall_ok,
        }
        return templates.TemplateResponse("index.html", context)
    except ClientError as e:
        return {"error": str(e)}
    except Exception as e:
        return {"error": f"Unhandled: {e}"}
