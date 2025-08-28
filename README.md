# AWS Network Validator
## Arsitektur Aplikasi

**Frontend (Website UI)**
- Dibuat dengan React.js atau Next.js.
- Menampilkan diagram jaringan (VPC, Subnet, Security Group, dll).
- Jika validasi berhasil → tampilkan centang hijau seperti di gambar.
- Jika gagal → tampilkan tanda silang atau warning.

**Backend (API Service)**
- Dibuat dengan Python (Flask / FastAPI) atau Node.js (Express).
- Bertugas untuk mengakses AWS API menggunakan AWS SDK:
- Python → boto3
- Node.js → aws-sdk

## Flow Pengecekan
**User buka website → Frontend memanggil API Backend.**
**Backend query AWS API:**
- describe-security-groups
- describe-route-tables
- describe-subnets
- describe-vpcs

**Backend memvalidasi aturan:**
- Web Server (public subnet) punya Security Group dengan inbound HTTP:80 dari internet.
- Database (private subnet) punya Security Group dengan inbound TCP:3306 hanya dari Web Server.
- Route Table public subnet punya route ke 0.0.0.0/0 via Internet Gateway.
- Route Table private subnet tidak punya route langsung ke Internet Gateway.
- Hasil dikembalikan ke frontend → frontend menampilkan diagram (✅ atau ❌).

How to deploy this app:
1. Install runtime di EC2
   ```bash
   sudo dnf update -y
   sudo dnf install -y python3.11 python3.11-pip git
   python3.11 -m pip install --upgrade pip
2. Buat folder app dan virtual env
   ```bash
   mkdir -p ~/aws-net-validator && cd ~/aws-net-validator
   python3.11 -m venv .venv
   source .venv/bin/activate
   pip install fastapi uvicorn[standard] boto3 jinja2
3. Set environment variable
   ```bash
   cat > ~/aws-net-validator/.env <<'EOF'
   # === AWS temporary credentials dari Learner Lab ===
   export AWS_ACCESS_KEY_ID=PASTE_FROM_LAB
   export AWS_SECRET_ACCESS_KEY=PASTE_FROM_LAB
   export AWS_SESSION_TOKEN=PASTE_FROM_LAB
   export AWS_REGION=ap-southeast-1

   # === Target yang ingin divalidasi ===
   export TARGET_VPC_ID=vpc-xxxxxxxx
   export PUBLIC_SUBNET_ID=subnet-xxxxxxxx
   export PRIVATE_SUBNET_ID=subnet-yyyyyyyy
   export WEB_SG_ID=sg-aaaaaaaa
   export DB_SG_ID=sg-bbbbbbbb
   EOF
4. Load variabelnya pada shell
   ```bash
   source ~/aws-net-validator/.env
5. Reverse proxy via nginx agar akses 80
   ```bash
   sudo dnf install -y nginx
   sudo tee /etc/nginx/conf.d/validator.conf >/dev/null <<'NG'
   server {
    listen 80;
    server_name _;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }
   }
   NG
   sudo nginx -t
   sudo systemctl enable --now nginx

6. Run aplikasi
   ```bash
   cd ~/aws-net-validator
   source .venv/bin/activate
   source .env
   uvicorn app.main:app --host 0.0.0.0 --port 8000





