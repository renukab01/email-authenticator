from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, EmailStr
import aiosmtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from redminelib import Redmine
import random
import time
from typing import Dict
import os
from dotenv import load_dotenv
import redis

load_dotenv()
app = FastAPI()

redis_client = redis.Redis(
    host=os.getenv('REDIS_HOST'),
    port=os.getenv('REDIS_PORT'),
    db=0,
    decode_responses=True,
    socket_timeout=5,
    socket_connect_timeout=5,
    connection_pool=redis.ConnectionPool(
        max_connections=10,
        host=os.getenv('REDIS_HOST'),
        port=os.getenv('REDIS_PORT'),
        db=0,
        decode_responses=True
    )
)

SMTP_SERVER = os.getenv('SMTP_SERVER')
SMTP_PORT = os.getenv('SMTP_PORT')
SMTP_USERNAME = os.getenv('SMTP_USERNAME')
SMTP_PASSWORD = os.getenv('SMTP_PASSWORD')   

redmine = Redmine(url=os.getenv('REDMINE_URL'), key=os.getenv('REDMINE_KEY'))

@app.on_event("startup")
async def startup_event():
    try:
        redis_client.ping()
    except redis.ConnectionError:
        raise Exception("Could not connect to Redis")

class EmailRequest(BaseModel):
    email: EmailStr

class OTPVerification(BaseModel):
    email: EmailStr
    otp: str

def generate_otp() -> str:
    """Generate and returns a 6-digit OTP"""
    return str(random.randint(100000, 999999))

def verify_email(email) -> bool:
    users = redmine.group.get(350, include=['users']).users
    user_emails = [redmine.user.get(user.id).mail for user in users]
    return email in user_emails

def store_in_redis(otp, email):
    key = f"otp:{email}"
    redis_client.hset(key, mapping={
        "otp" : otp,
        "timestamp" : str(time.time())
    })
    redis_client.expire(key, 300)

async def send_email(to_email: str, otp: str) -> bool:
    """Send OTP via email asynchronously"""
    try:
        message = MIMEMultipart()
        message["From"] = SMTP_USERNAME
        message["To"] = to_email
        message["Subject"] = "Your OTP for Email Verification for Tekdi HR Bot"
        
        body = f"Your OTP is {otp}\nThis OTP is valid for 5 minutes."
        message.attach(MIMEText(body, "plain"))

        smtp = aiosmtplib.SMTP(hostname=SMTP_SERVER, port=SMTP_PORT, use_tls=False)
        await smtp.connect()
        await smtp.starttls()
        await smtp.login(SMTP_USERNAME, SMTP_PASSWORD)
        
        await smtp.send_message(message)
        await smtp.quit()
        
        return True
    except Exception as e:
        print(f"Error sending email: {e}")
        return False

@app.post("/send-otp/")
async def send_otp(email_request: EmailRequest):
    """Send OTP to the provided email"""
    email = email_request.email
    if not verify_email(email):
        raise HTTPException(status_code=500, detail="Invalid email")
    otp = generate_otp()

    store_in_redis(otp,email)

    if not await send_email(email, otp):
        raise HTTPException(status_code=500, detail="Failed to send OTP")
    
    return {"message": "OTP sent successfully", "otp":otp}

@app.post("/verify-otp/")
async def verify_otp(verification: OTPVerification):
    """Verify the OTP entered by the user"""
    email = verification.email
    user_otp = verification.otp
    

    key = f"otp:{email}"
    stored_data = redis_client.hgetall(key)
    if not stored_data:
        raise HTTPException(status_code=400, detail="No OTP found for this email")
    
    stored_otp = stored_data["otp"]
    timestamp = float(stored_data["timestamp"])
    
    if time.time() - timestamp > 300:
        redis_client.delete(key)
        raise HTTPException(status_code=400, detail="OTP has expired")
    
    if user_otp != stored_otp:
        raise HTTPException(status_code=400, detail="Invalid OTP")
    
    redis_client.delete(key)
    
    return {
        "message": "Email verified successfully",
        "email": email
    }

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)