import httpx
import json
from datetime import datetime, timedelta
import redis.asyncio as redis
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from jose import jwt, JWTError

from app.config import settings
from app.schemas.payment import UserAuthResponse
from app.utils.retry import async_retry
from app.core.logging import logger

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="token")

# Initialize Redis client for caching
# This connection is now managed here for auth caching purposes.
redis_client = redis.from_url(settings.REDIS_URL, decode_responses=True)

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
                response = await client.post(
                    f"{settings.USER_MANAGEMENT_URL.rstrip('/')}/auth/verify",
                    headers={"Authorization": f"Bearer {jwt_token}"},
                    timeout=5
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
    if current_user.role != "Owner":
        logger.warning("Attempt to perform owner action by non-owner.", user_id=current_user.user_id, role=current_user.role)
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Only Owners can perform this action")
    return current_user