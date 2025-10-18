import logging
from typing import Dict

from app.config import settings
from app.schemas.payment import NotificationPayload
from app.utils.retry import async_retry
import httpx

from app.core.logging import logger

class NotificationService:
    def __init__(self):
        self.base_url = settings.NOTIFICATION_SERVICE_URL

    async def _send_external_notification(self, payload: NotificationPayload):
        """Sends notification to the external Notification Service."""
        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(f"{self.base_url}/notifications/send", json=payload.model_dump(), timeout=5)
                response.raise_for_status()
                logger.info("Notification sent successfully via external service", user_id=payload.user_id, subject=payload.subject)
                return True
        except httpx.RequestError as exc:
            logger.error("Notification service request error, falling back to mock", user_id=payload.user_id, error=str(exc))
            return False
        except httpx.HTTPStatusError as exc:
            logger.error("Notification service HTTP error, falling back to mock", user_id=payload.user_id, status_code=exc.response.status_code, response_text=exc.response.text)
            return False

    def _get_template(self, lang: str, template_name: str) -> Dict[str, str]:
        templates = {
            "en": {
                "payment_initiated": {
                    "subject": "Payment Initiated - Action Required",
                    "message": "Dear Landlord, your payment for property {property_id} has been initiated. Please complete the payment of 100 ETB via CBE Birr or HelloCash using the link: {payment_link}"
                },
                "payment_success": {
                    "subject": "Payment Successful!",
                    "message": "Dear Landlord, your payment for property {property_id} was successful. Your listing is now approved."
                },
                "payment_failed": {
                    "subject": "Payment Failed - Action Required",
                    "message": "Dear Landlord, your payment for property {property_id} has failed. Please try again."
                },
                "payment_timed_out": {
                    "subject": "Payment Timed Out - Action Required",
                    "message": "Dear Landlord, your pending payment for property {property_id} has timed out and failed. Please try again."
                },
                "health_alert": {
                    "subject": "Service Health Status",
                    "message": "Payment Processing Microservice is currently {status}. Details: {details}"
                }
            },
            "am": {
                "payment_initiated": {
                    "subject": "ክፍያ ተጀምሯል - እርምጃ ያስፈልጋል",
                    "message": "ውድ የቤት ባለቤት፣ ለንብረትዎ {property_id} ክፍያ ተጀምሯል። እባክዎ 100 ብር በ CBE Birr ወይም HelloCash በዚህ ሊንክ ያጠናቅቁ፡ {payment_link}"
                },
                "payment_success": {
                    "subject": "ክፍያ ተሳክቷል!",
                    "message": "ውድ የቤት ባለቤት፣ ለንብረትዎ {property_id} ክፍያ በተሳካ ሁኔታ ተጠናቋል። ማስታወቂያዎ አሁን ጸድቋል።"
                },
                "payment_failed": {
                    "subject": "ክፍያ አልተሳካም - እርምጃ ያስፈልጋል",
                    "message": "ውድ የቤት ባለቤት፣ ለንብረትዎ {property_id} ክፍያ አልተሳካም። እባክዎ እንደገና ይሞክሩ።"
                },
                "payment_timed_out": {
                    "subject": "ክፍያ ጊዜው አልፏል - እርምጃ ያስፈልጋል",
                    "message": "ውድ የቤት ባለቤት፣ ለንብረትዎ {property_id} በመጠባበቅ ላይ የነበረው ክፍያ ጊዜው አልፏል እና አልተሳካም። እባክዎ እንደገና ይሞክሩ።"
                },
                "health_alert": {
                    "subject": "የአገልግሎት ጤና ሁኔታ",
                    "message": "የክፍያ ማቀናበሪያ ማይክሮ አገልግሎት በአሁኑ ጊዜ {status} ነው። ዝርዝሮች፡ {details}"
                }
            },
            "om": {
                "payment_initiated": {
                    "subject": "Kaffaltiin Jalqabameera - Tarkaanfii Barbaachisaadha",
                    "message": "Jiraataa kabajamaa, kaffaltiin keessan kan qabeenya {property_id} jalqabameera. Maaloo kaffaltii 100 ETB CBE Birr ykn HelloCashn linkii kanaan xumuraa: {payment_link}"
                },
                "payment_success": {
                    "subject": "Kaffaltiin Milkaa'eera!",
                    "message": "Jiraataa kabajamaa, kaffaltiin keessan kan qabeenya {property_id} milkaa'eera. Galmeen keessan amma mirkanaa'eera."
                },
                "payment_failed": {
                    "subject": "Kaffaltiin Milkaa'uu Dide - Tarkaanfii Barbaachisaadha",
                    "message": "Jiraataa kabajamaa, kaffaltiin keessan kan qabeenya {property_id} milkaa'uu dideera. Maaloo deebisanii yaalaa."
                },
                "payment_timed_out": {
                    "subject": "Kaffaltiin Yeroo Isaa Darbe - Tarkaanfii Barbaachisaadha",
                    "message": "Jiraataa kabajamaa, kaffaltiin keessan kan qabeenya {property_id} yeroo isaa darbeera. Maaloo deebisanii yaalaa."
                },
                "health_alert": {
                    "subject": "Haala Fayyaa Tajaajilaa",
                    "message": "Tajaajilli Xiqqaa Qindeessaa Kaffaltii yeroo ammaa {status} dha. Bal'ina: {details}"
                }
            }
        }
        return templates.get(lang, templates["en"]).get(template_name, templates["en"][template_name])

    async def send_notification(
        self, 
        user_id: str,
        email: str,
        phone_number: str,
        preferred_language: str,
        template_name: str,
        template_vars: Dict[str, str]
    ):
        """
        Sends a notification, attempting to use the external service first, then falling back to mock.
        """
        lang = preferred_language.lower() if preferred_language else "en"
        template = self._get_template(lang, template_name)

        message = template["message"].format(**template_vars)
        subject = template["subject"].format(**template_vars)

        payload = NotificationPayload(
            user_id=user_id,
            email=email,
            phone_number=phone_number,
            preferred_language=preferred_language,
            message=message,
            subject=subject
        )

        # Attempt to send via external service first
        if await self._send_external_notification(payload):
            return

        # Fallback to mock logging if external service fails
        logger.warning("Falling back to mock notification (logging only)", user_id=user_id, email=email, subject=subject, message=message)
        # In a real scenario, you might store this in a DB for later retry or send via a different channel

notification_service = NotificationService()
