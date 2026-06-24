FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY scrape.py scrape_notifications.py scrape_prospects_details.py services.py openai_api.py ./

CMD ["python", "scrape.py"]

