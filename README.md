# Financial News Aggregator API

Single FastAPI endpoint that aggregates **124+ RSS feeds** from 25+ publishers, stores in MongoDB with dedup, and serves via a smart-cached API.

## Architecture

```
┌─────────────────────────────────────────────────────┐
│                   FastAPI Server                     │
│                                                     │
│  Background Scheduler (every 10 min)                │
│  ├── Fetches 124+ RSS feeds in PARALLEL (50 workers)│
│  ├── Parses title, desc, source, pubDate            │
│  ├── Converts all timestamps to IST                 │
│  └── Stores in MongoDB (link = unique, no dupes)    │
│                                                     │
│  GET /news endpoint                                 │
│  ├── ?company=Wipro → Google RSS + DB search        │
│  ├── Within 5 min cache → serve from DB only        │
│  ├── After 5 min cache → full fetch + serve         │
│  └── Returns: title, description, source, time, link│
└──────────────────┬──────────────────────────────────┘
                   │
                   ▼
          ┌────────────────┐
          │    MongoDB      │
          │                │
          │  articles coll │
          │  unique: link  │
          └────────────────┘
```

## API Usage

### Get all financial news (last 24 hours)
```
GET /news
GET /news?hours=48&page=1&limit=100
```

### Search specific company
```
GET /news?company=Wipro
GET /news?company=HDFC Bank&hours=72&limit=200
GET /news?company=Reliance Industries&page=2
```

### Response format
```json
{
  "success": true,
  "query": { "company": "Wipro", "hours": 24, "page": 1, "limit": 50 },
  "meta": {
    "total_results": 342,
    "returned": 50,
    "total_pages": 7,
    "feeds_configured": 124,
    "last_full_fetch_ist": "2026-04-14 15:30:00 IST",
    "cache_status": "fresh (from DB)"
  },
  "articles": [
    {
      "title": "Wipro Q4 Results: Net profit rises 15%",
      "description": "IT major Wipro reported a 15% rise in...",
      "source": "Economic Times",
      "published_ist": "2026-04-14 14:22:00 IST",
      "link": "https://economictimes.indiatimes.com/..."
    }
  ]
}
```

### Health check
```
GET /health
```

## Smart Caching Logic

```
Request comes in
    │
    ├── Has company name?
    │   ├── YES → Always fetch Google News RSS for that company
    │   │         └── Last full fetch < 5 min ago?
    │   │             ├── YES → Return company results from DB (fast)
    │   │             └── NO  → Also run full 124-feed fetch, then return
    │   │
    │   └── NO → Last full fetch < 5 min ago?
    │            ├── YES → Return all news from DB (fast, ~50ms)
    │            └── NO  → Run full 124-feed fetch, then return
    │
    Background scheduler runs every 10 min regardless
```

## EC2 Deployment

### 1. Launch EC2 Instance
- **AMI:** Ubuntu 24.04 LTS
- **Type:** t3.medium (2 vCPU, 4GB RAM) minimum
- **Storage:** 20GB+ (for MongoDB data)
- **Security Group:** Open ports 8000 (API) and 22 (SSH)

### 2. Install Dependencies
```bash
# SSH into EC2
ssh -i your-key.pem ubuntu@your-ec2-ip

# Update system
sudo apt update && sudo apt upgrade -y

# Install Python 3.11+
sudo apt install -y python3 python3-pip python3-venv

# Install MongoDB
# (Option A: Local MongoDB)
sudo apt install -y gnupg curl
curl -fsSL https://www.mongodb.org/static/pgp/server-7.0.asc | sudo gpg -o /usr/share/keyrings/mongodb-server-7.0.gpg --dearmor
echo "deb [ signed-by=/usr/share/keyrings/mongodb-server-7.0.gpg ] https://repo.mongodb.org/apt/ubuntu jammy/mongodb-org/7.0 multiverse" | sudo tee /etc/apt/sources.list.d/mongodb-org-7.0.list
sudo apt update
sudo apt install -y mongodb-org
sudo systemctl start mongod
sudo systemctl enable mongod

# (Option B: Use MongoDB Atlas free tier — just set MONGO_URI env var)
```

### 3. Deploy the App
```bash
# Clone/upload your project
mkdir ~/financial-news-api && cd ~/financial-news-api
# Upload main.py, feeds_config.py, requirements.txt

# Create virtual environment
python3 -m venv venv
source venv/bin/activate

# Install packages
pip install -r requirements.txt

# Test run
python main.py
# Visit http://your-ec2-ip:8000/docs for Swagger UI
```

### 4. Run as Service (systemd)
```bash
sudo tee /etc/systemd/system/newsapi.service << 'EOF'
[Unit]
Description=Financial News Aggregator API
After=network.target mongod.service

[Service]
Type=simple
User=ubuntu
WorkingDirectory=/home/ubuntu/financial-news-api
Environment="MONGO_URI=mongodb://localhost:27017"
Environment="DB_NAME=financial_news"
ExecStart=/home/ubuntu/financial-news-api/venv/bin/uvicorn main:app --host 0.0.0.0 --port 8000
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable newsapi
sudo systemctl start newsapi
sudo systemctl status newsapi

# View logs
sudo journalctl -u newsapi -f
```

### 5. Optional: Nginx Reverse Proxy
```bash
sudo apt install -y nginx

sudo tee /etc/nginx/sites-available/newsapi << 'EOF'
server {
    listen 80;
    server_name your-domain.com;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_read_timeout 120s;
    }
}
EOF

sudo ln -s /etc/nginx/sites-available/newsapi /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl restart nginx
```

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `MONGO_URI` | `mongodb://localhost:27017` | MongoDB connection string |
| `DB_NAME` | `financial_news` | Database name |

## Feeds Covered (124+)

| Publisher | Feeds | Articles/day |
|-----------|-------|-------------|
| Economic Times | 15 | ~200+ |
| Moneycontrol | 12 | ~150+ |
| Business Standard | 22 | ~100+ |
| Livemint | 10 | ~80+ |
| The Hindu / BL | 8 | ~60+ |
| CNBC TV18 | 5 | ~50+ |
| Financial Express | 5 | ~50+ |
| Indian Express | 5 | ~40+ |
| NDTV Profit | 2 | ~80+ |
| Times of India | 2 | ~30+ |
| Business Today | 3 | ~30+ |
| Other Indian (12) | 12 | ~100+ |
| Aggregators (Pulse, Google News, Investing.com) | 10 | ~200+ |
| Global (Reuters, CNBC US, Bloomberg, etc.) | 10 | ~100+ |
| NSE Exchange | 3 | varies |
| **Total** | **124** | **~1500+/day** |
