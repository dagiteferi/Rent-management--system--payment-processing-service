import json
from datetime import datetime, timedelta
import redis.asyncio as redis
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer, APIKeyHeader
from jose import jwt, JWTError
from app.config import settings
from app.schemas.payment import UserAuthResponse
from app.utils.retry import async_retry
from app.core.security import logger, decrypt_data # Import decrypt_data

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="token")

api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)

redis_client = redis.from_url(settings.REDIS_URL, decode_responses=True)

async def get_api_key(api_key: str = Depends(api_key_header)) -> str:
    if api_key is None:
        return None # API key not provided
    
    if api_key == settings.PAYMENT_SERVICE_API_KEY:
        logger.info("API Key authentication successful.")
        return api_key
    else:
        logger.warning("Invalid API Key provided.", provided_key_prefix=api_key[:10] + "...")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid API Key"
        )

async def get_current_user(token: str = Depends(oauth2_scheme)) -> UserAuthResponse:
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )

    # 1. Check cache first
    try:
        cached_user_data = await redis_client.get(f"user_cache:{token}")
        if cached_user_data:
            logger.info("User data retrieved from cache.")
            return UserAuthResponse(**json.loads(cached_user_data))
    except Exception as e:
        logger.error("Redis cache read failed, proceeding to verification.", error=str(e))
        # If cache read fails for any reason, we'll just proceed to normal verification.

    # If not in cache, proceed to decode the token and verify with the User Management service.
    try:
        payload = jwt.decode(token, settings.JWT_SECRET, algorithms=[settings.JWT_ALGORITHM], options={"verify_exp": True})
        user_id: str = payload.get("sub")
        if user_id is None:
            logger.warning("JWT payload missing user_id (sub claim).")
            raise credentials_exception
        
        # Calculate token expiry for cache TTL (Time To Live)
        exp_timestamp = payload.get("exp")
        if exp_timestamp:
            # Ensure timestamps are timezone-aware (UTC) for correct calculation
            expires_delta = datetime.utcfromtimestamp(exp_timestamp) - datetime.utcnow()
        else:
            # Fallback if 'exp' claim is not present, though it should be for security.
            # Use a reasonable default cache time.
            expires_delta = timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)

    except JWTError as e:
        logger.warning("JWT decoding failed.", error=str(e))
        raise credentials_exception

    # 2. Verify token with User Management Microservice
    @async_retry(max_attempts=3, delay=1, exceptions=(httpx.RequestError, HTTPException))
    async def verify_with_user_management(jwt_token: str):
        async with httpx.AsyncClient() as client:
            try:
                response = await client.get(
                    f"{settings.USER_MANAGEMENT_URL.rstrip('/')}/auth/verify",
                    headers={"Authorization": f"Bearer {jwt_token}"},
                    timeout=30
                )
                response.raise_for_status()
                return response.json()
            except httpx.RequestError as exc:
                logger.error("User Management service unavailable.", error=str(exc), user_id=user_id)
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail=f"User Management service is unavailable: {exc}"
                )
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code == status.HTTP_401_UNAUTHORIZED:
                    logger.warning("User Management returned 401 for token verification.", user_id=user_id)
                    raise credentials_exception
                elif exc.response.status_code == status.HTTP_403_FORBIDDEN:
                    logger.warning("User Management returned 403 for token verification.", user_id=user_id)
                    raise HTTPException(
                        status_code=status.HTTP_403_FORBIDDEN,
                        detail="Not authorized to perform this action"
                    )
                else:
                    logger.error("User Management service error.", status_code=exc.response.status_code, response_text=exc.response.text, user_id=user_id)
                    raise HTTPException(
                        status_code=exc.response.status_code,
                        detail=f"User Management service error: {exc.response.text}"
                    )

    user_data = await verify_with_user_management(token)
    
    # Decrypt phone_number if it exists and is encrypted
    if "phone_number" in user_data and user_data["phone_number"]:
        original_phone_number = user_data["phone_number"]
        try:
            decrypted_phone_number = decrypt_data(original_phone_number)
            user_data["phone_number"] = decrypted_phone_number
        except Exception: # Catch any exception from decrypt_data
            # If decryption fails, assume it's either not encrypted or malformed.
            # Use the original phone number as is.
            logger.warning("Phone number decryption failed, using original value. This might indicate an unencrypted phone number or a decryption issue.", user_id=user_id)
            user_data["phone_number"] = original_phone_number
    
    # 3. Store result in cache
    try:
        if expires_delta.total_seconds() > 0:
            await redis_client.set(f"user_cache:{token}", json.dumps(user_data), ex=int(expires_delta.total_seconds()))
            logger.info("User data cached successfully.", user_id=user_id)
    except Exception as e:
        logger.error("Redis cache write failed.", error=str(e), user_id=user_id)

    logger.info("User verified successfully via service.", user_id=user_id, role=user_data.get("role"))
    return UserAuthResponse(**user_data)


async def get_current_owner(current_user: UserAuthResponse = Depends(get_current_user)) -> UserAuthResponse:
    if current_user.role.lower() != "owner":
        logger.warning("Attempt to perform owner action by non-owner.", user_id=current_user.user_id, role=current_user.role)
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Only Owners can perform this action")
    return current_user

async def get_authenticated_entity(
    api_key: Optional[str] = Depends(get_api_key),
    token: Optional[str] = Depends(oauth2_scheme)
) -> UserAuthResponse:
    """
    Authenticates a request using either an API Key (for service-to-service)
    or a JWT token (for owner users).
    """
    if api_key:
        # If API key is present and valid, it's a service-to-service call.
        # Return a dummy UserAuthResponse with a 'Service' role.
        # The actual user_id for the payment will come from the request body.
        logger.info("Request authenticated via API Key.")
        return UserAuthResponse(
            user_id=uuid.uuid4(), # Placeholder, actual user_id comes from payload
            role="Service",
            email="service@example.com", # Placeholder
            phone_number="+251900000000", # Placeholder
            preferred_language="en" # Placeholder
        )
    
    if token:
        try:
            # If no API key, or API key was not valid, try JWT authentication for owner.
            current_owner = await get_current_owner(token)
            logger.info("Request authenticated via JWT (Owner role).")
            return current_owner
        except HTTPException as e:
            # If JWT authentication fails, re-raise the exception
            logger.warning("JWT authentication failed.", detail=e.detail)
            raise e
    
    # If neither API key nor valid JWT owner token is provided, raise unauthorized.
    logger.warning("Authentication failed: Neither valid API Key nor Owner JWT provided.")
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Not authenticated: Provide a valid API Key or Owner JWT"
    )