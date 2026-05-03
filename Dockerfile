FROM python:3.11-slim
ENV PYTHONUNBUFFERED=1
WORKDIR /app
RUN pip install flask requests beautifulsoup4 google-genai pytz
COPY . .
EXPOSE 5000
CMD ["python", "-u", "app.py"]
