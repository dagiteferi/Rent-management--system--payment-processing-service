import httpx
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm

from app.config import settings
from app.core.logging import logger

router = APIRouter()

@router.post("/token", summary="Get access token")
async def login_for_access_token(form_data: OAuth2PasswordRequestForm = Depends()):
    """
    Logs in a user and returns an access token.

    This endpoint proxies the authentication request to the User Management microservice.
    """
    async with httpx.AsyncClient() as client:
        try:
            # The User Management service expects the credentials in a specific format.
            # Typically, this would be form data, similar to what this endpoint receives.
            response = await client.post(
                f"{settings.USER_MANAGEMENT_URL.rstrip('/')}/api/v1/auth/login",
                data={"username": form_data.username, "password": form_data.password},
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                timeout=10
            )

            # Check if the request to the User Management service was successful
            if response.status_code == status.HTTP_401_UNAUTHORIZED:
                logger.warning("Invalid credentials provided for user.", username=form_data.username)
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Incorrect username or password",
                    headers={"WWW-Authenticate": "Bearer"},
                )
            
            response.raise_for_status()  # Raise an exception for other 4xx/5xx responses

            # Return the exact response from the User Management service
            return response.json()

        except httpx.RequestError as exc:
            logger.error("Failed to connect to User Management service for token generation.", error=str(exc))
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Authentication service is currently unavailable."
            )
        except httpx.HTTPStatusError as exc:
            logger.error(
                "Error response from User Management service during token generation.",
                status_code=exc.response.status_code,
                response_text=exc.response.text
            )
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="Failed to authenticate with the user service."
            )
