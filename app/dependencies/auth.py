import httpx
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="token") # This tokenUrl is a placeholder, actual auth is via User Management
from jose import jwt, JWTError
from app.config import settings
from app.schemas.payment import UserAuthResponse
from app.utils.retry import async_retry
from app.core.logging import logger # Import structured logger

oauth2_scheme = OAuth2Bearer(tokenUrl="token") # This tokenUrl is a placeholder, actual auth is via User Management

async def get_current_user(token: str = Depends(oauth2_scheme)) -> UserAuthResponse:
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        # Decode JWT locally first to get basic info like user_id if needed, or just pass to User Management
        # For this project, we'll rely on User Management for full validation and user data retrieval.
        payload = jwt.decode(token, settings.JWT_SECRET, algorithms=["HS256"])
        # We don't fully trust the payload here, User Management will be the source of truth
        user_id: str = payload.get("sub")
        if user_id is None:
            logger.warning("JWT payload missing user_id (sub claim)")
            raise credentials_exception
    except JWTError as e:
        logger.warning("JWT decoding failed", error=str(e))
        raise credentials_exception

    # Verify token with User Management Microservice
    @async_retry(max_attempts=3, delay=1, exceptions=(httpx.RequestError, HTTPException))
    async def verify_with_user_management(jwt_token: str):
        async with httpx.AsyncClient() as client:
            try:
                response = await client.post(
                    f"{settings.USER_MANAGEMENT_URL}/auth/verify",
                    headers={
                        "Authorization": f"Bearer {jwt_token}"
                    },
                    timeout=5 # Add a timeout for the request
                )
                response.raise_for_status() # Raise an exception for 4xx/5xx responses
                return response.json()
            except httpx.RequestError as exc:
                logger.error("User Management service unavailable", error=str(exc), user_id=user_id)
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail=f"User Management service is unavailable: {exc}"
                )
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code == status.HTTP_401_UNAUTHORIZED:
                    logger.warning("User Management returned 401 for token verification", user_id=user_id)
                    raise credentials_exception
                elif exc.response.status_code == status.HTTP_403_FORBIDDEN:
                    logger.warning("User Management returned 403 for token verification", user_id=user_id)
                    raise HTTPException(
                        status_code=status.HTTP_403_FORBIDDEN,
                        detail="Not authorized to perform this action"
                    )
                else:
                    logger.error("User Management service error", status_code=exc.response.status_code, response_text=exc.response.text, user_id=user_id)
                    raise HTTPException(
                        status_code=exc.response.status_code,
                        detail=f"User Management service error: {exc.response.text}"
                    )

    user_data = await verify_with_user_management(token)
    logger.info("User verified successfully", user_id=user_id, role=user_data.get("role"))
    return UserAuthResponse(**user_data)

async def get_current_owner(current_user: UserAuthResponse = Depends(get_current_user)) -> UserAuthResponse:
    if current_user.role != "Owner":
        logger.warning("Attempt to perform owner action by non-owner", user_id=current_user.user_id, role=current_user.role)
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Only Owners can perform this action")
    return current_user

