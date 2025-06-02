from passlib.context import CryptContext

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

password = "your_password_here"
hashed = pwd_context.hash(password)
print(f"Hashed password: {hashed}") 