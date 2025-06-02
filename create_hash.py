#!/usr/bin/env python3

try:
    from passlib.context import CryptContext
    
    pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
    
    password = "testpassword123"
    new_hash = pwd_context.hash(password)
    
    print(f"Password: {password}")
    print(f"New hash: {new_hash}")
    
    # Test verification
    is_valid = pwd_context.verify(password, new_hash)
    print(f"Verification test: {is_valid}")
    
    if is_valid:
        print("\n✅ Hash generation successful!")
        print(f"\nRun this SQL to update your user:")
        print(f"UPDATE users SET password_hash = '{new_hash}' WHERE email = 'test@company.com';")
    else:
        print("❌ Hash verification failed")
        
except Exception as e:
    print(f"Error with bcrypt: {e}")
    print("Let's use a simpler approach...")
    
    # Fallback: use hashlib for testing (NOT for production)
    import hashlib
    password = "testpassword123"
    simple_hash = hashlib.sha256(password.encode()).hexdigest()
    print(f"Simple hash: {simple_hash}") 