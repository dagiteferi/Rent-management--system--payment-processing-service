# Payment Processing Microservice

This microservice handles payment initiation, verification, and status updates for a Rental Management System, using FastAPI, PostgreSQL, and Chapa.co's sandbox API.

## Table of Contents
- [Setup](#setup)
- [Environment Variables](#environment-variables)
- [Database Setup](#database-setup)
- [Running the Application](#running-the-application)
- [API Endpoints](#api-endpoints)
- [Chapa Sandbox Setup](#chapa-sandbox-setup)
- [Frontend Integration Guidance](#frontend-integration-guidance)
- [Demo Walkthrough](#demo-walkthrough)
- [Testing](#testing)

## Setup

1.  **Clone the repository:**
    ```bash
    git clone <repository_url>
    cd Rent-management--system -payment-processing-service
    ```

2.  **Create a virtual environment and install dependencies:**
    ```bash
    python3.10 -m venv venv
    source venv/bin/activate
    pip install -r requirements.txt
    ```

## Environment Variables

Create a `.env` file in the root directory based on `.env.example`:

```ini
CHAPA_API_KEY="your_chapa_api_key_here"
CHAPA_SECRET_KEY="your_chapa_secret_key_here"
CHAPA_WEBHOOK_SECRET="your_chapa_webhook_secret_key_here" # Used for HMAC-SHA256 webhook verification
JWT_SECRET="your_jwt_secret_key_here"
USER_MANAGEMENT_URL="http://user-management:8000/api/v1"
DATABASE_URL="postgresql+asyncpg://user:password@host:port/database"
NOTIFICATION_SERVICE_URL="http://notification-service:8000/api/v1"
PROPERTY_LISTING_SERVICE_URL="http://property-listing-service:8000/api/v1"
ENCRYPTION_KEY="a_32_byte_secret_key_for_aes_encryption" # Must be 32 bytes for AES-256
REDIS_URL="redis://localhost:6379/0" # For rate limiting and optional caching
```

## Database Setup

Ensure you have a PostgreSQL database running and accessible. Update the `DATABASE_URL` in your `.env` file.

Run the migration script to create the `Payments` table and seed initial data:

```bash
./migrate.sh
```

## Running the Application

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

## API Endpoints

(Documentation will be generated here)

## Chapa Sandbox Setup

1.  Sign up for a Chapa.co sandbox account.
2.  Obtain your `CHAPA_API_KEY`, `CHAPA_SECRET_KEY`, and configure a `CHAPA_WEBHOOK_SECRET` for signature verification.
3.  Configure a webhook URL in your Chapa dashboard to point to `YOUR_SERVICE_PUBLIC_URL/api/v1/webhook/chapa`.

## Frontend Integration Guidance

Here are React.js snippets for integrating with the Payment Processing Microservice:

### 1. Initiating a Payment

```jsx
// components/InitiatePaymentButton.jsx
import React, { useState } from 'react';
import axios from 'axios';
import { v4 as uuidv4 } from 'uuid'; // For generating request_id

const InitiatePaymentButton = ({ propertyId, userId, amount, jwtToken }) => {
  const [loading, setLoading] = useState(false);
  const [paymentLink, setPaymentLink] = useState(null);
  const [error, setError] = useState(null);

  const handleInitiatePayment = async () => {
    setLoading(true);
    setError(null);
    setPaymentLink(null);

    try {
      const response = await axios.post(
        '/api/v1/payments/initiate', // Adjust base URL as needed
        {
          request_id: uuidv4(), // Unique ID for idempotency
          property_id: propertyId,
          user_id: userId,
          amount: amount,
        },
        {
          headers: {
            Authorization: `Bearer ${jwtToken}`,
            'Content-Type': 'application/json',
          },
        }
      );
      setPaymentLink(response.data.chapa_tx_ref); // chapa_tx_ref will contain the checkout URL
    } catch (err) {
      console.error('Error initiating payment:', err);
      setError(err.response?.data?.detail || 'Failed to initiate payment');
    } finally {
      setLoading(false);
    }
  };

  return (
    <div>
      <button onClick={handleInitiatePayment} disabled={loading}>
        {loading ? 'Initiating...' : 'Pay to Post Listing'}
      </button>
      {paymentLink && (
        <p>
          Payment initiated! Complete your payment here:
          <a href={paymentLink} target="_blank" rel="noopener noreferrer">
            {paymentLink}
          </a>
        </p>
      )}
      {error && <p style={{ color: 'red' }}>Error: {error}</p>}
    </div>
  );
};

export default InitiatePaymentButton;
```

### 2. Polling Payment Status

```jsx
// components/PaymentStatusChecker.jsx
import React, { useState, useEffect } from 'react';
import axios from 'axios';

const PaymentStatusChecker = ({ paymentId, jwtToken }) => {
  const [status, setStatus] = useState('UNKNOWN');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);

  const fetchPaymentStatus = async () => {
    setLoading(true);
    setError(null);
    try {
      const response = await axios.get(
        `/api/v1/payments/${paymentId}/status`, // Adjust base URL as needed
        {
          headers: {
            Authorization: `Bearer ${jwtToken}`,
          },
        }
      );
      setStatus(response.data.status);
      return response.data.status;
    } catch (err) {
      console.error('Error fetching payment status:', err);
      setError(err.response?.data?.detail || 'Failed to fetch status');
      return 'ERROR';
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    if (!paymentId || !jwtToken) return;

    let intervalId;

    const pollStatus = async () => {
      const currentStatus = await fetchPaymentStatus();
      if (currentStatus === 'SUCCESS' || currentStatus === 'FAILED' || currentStatus === 'ERROR') {
        clearInterval(intervalId);
      }
    };

    // Initial fetch
    pollStatus();

    // Poll every 5 seconds until success or failure
    intervalId = setInterval(pollStatus, 5000);

    return () => clearInterval(intervalId);
  }, [paymentId, jwtToken]);

  return (
    <div>
      <h3>Payment Status for ID: {paymentId}</h3>
      <p>Current Status: <strong>{status}</strong></p>
      {loading && <p>Checking status...</p>}
      {error && <p style={{ color: 'red' }}>Error: {error}</p>}
      {(status === 'SUCCESS' || status === 'FAILED') && (
        <p>Payment process completed.</p>
      )}
    </div>
  );
};

export default PaymentStatusChecker;
```

## Demo Walkthrough

This section provides a step-by-step guide for demonstrating the Payment Processing Microservice during a school presentation.

**Prerequisites:**
*   The Payment Processing Microservice is running (e.g., `uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload`).
*   A PostgreSQL database is set up and migrated (`./migrate.sh`).
*   `.env` file is correctly configured with `CHAPA_API_KEY`, `CHAPA_SECRET_KEY`, `CHAPA_WEBHOOK_SECRET`, `USER_MANAGEMENT_URL`, etc.
*   Chapa.co sandbox account is set up, and a webhook URL pointing to your public service endpoint (`YOUR_SERVICE_PUBLIC_URL/api/v1/webhook/chapa`) is configured.
*   (Conceptual) A User Management Microservice is running and can issue JWTs for an 'Owner' role.
*   (Conceptual) A Property Listing Microservice is running.

**Scenario: Landlord Posts a Property Listing**

1.  **Login as an Owner (Conceptual):**
    *   Explain that a landlord (Owner) would first log into the Rental Management System, obtaining a JWT token from the User Management Microservice.
    *   *For demo:* Assume you have a valid JWT for an Owner user (e.g., `eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...`).

2.  **Initiate Payment for a Listing:**
    *   The landlord decides to post a new property listing. This action requires a payment.
    *   **API Call (using `curl` or Postman):**
        ```bash
        # Replace <OWNER_JWT_TOKEN>, <PROPERTY_UUID>, <OWNER_USER_UUID> with actual values
        # Generate a new UUID for request_id for each initiation attempt
        curl -X POST "http://localhost:8000/api/v1/payments/initiate" \
             -H "Authorization: Bearer <OWNER_JWT_TOKEN>" \
             -H "Content-Type: application/json" \
             -d '{ 
                   "request_id": "<GENERATE_NEW_UUID_HERE>", 
                   "property_id": "<PROPERTY_UUID>", 
                   "user_id": "<OWNER_USER_UUID>", 
                   "amount": 100.00 
                 }'
        ```
    *   **Expected Output:** A `202 Accepted` response containing the `payment_id` and a `chapa_tx_ref` which is the Chapa checkout URL. Note down the `payment_id` and the `checkout_url`.
    *   **Demonstrate Idempotency:** Make the *exact same* `curl` request again (same `request_id`). Show that the service returns the *same* `payment_id` and `checkout_url`, and no new payment record is created in the database (you can verify this by checking the database directly or observing logs). This highlights that the payment was not re-initiated.

3.  **Complete Payment via Chapa Sandbox:**
    *   Open the `checkout_url` obtained in the previous step in a web browser.
    *   Explain that this is the Chapa.co sandbox payment page.
    *   Select a payment method (e.g., CBE Birr, Telebirr, or a test card like `4111 1111 1111 1111` for Visa).
    *   Complete the payment process. Chapa will simulate a successful transaction.
    *   Explain that upon successful payment, Chapa will redirect back to the `return_url` configured during initiation (e.g., your frontend's payment status page).

4.  **Verify Webhook Processing:**
    *   Show the microservice's console/logs. You should see log entries indicating: `Received Chapa webhook`, `Chapa webhook signature verified successfully`, `Chapa payment verification successful`, `Payment status updated to SUCCESS`, and `Property approved via Property Listing Service.`
    *   This demonstrates the automated update of payment status and the trigger for listing approval.

5.  **Check Payment Status (Frontend Polling Simulation):**
    *   **API Call (using `curl` or Postman):**
        ```bash
        # Replace <OWNER_JWT_TOKEN> and <PAYMENT_ID> from step 2
        curl -X GET "http://localhost:8000/api/v1/payments/<PAYMENT_ID>/status" \
             -H "Authorization: Bearer <OWNER_JWT_TOKEN>"
        ```
    *   **Expected Output:** A `200 OK` response with `status: SUCCESS`.
    *   Explain that a frontend application would typically poll this endpoint to update the user interface in real-time, as shown in the `PaymentStatusChecker.jsx` snippet in the Frontend Integration Guidance section.

6.  **Verify Listing Approval (Conceptual):**
    *   Explain that at this point, the Property Listing Microservice would have received the approval signal and marked the property as available for tenant searches.

7.  **Demonstrate Multilingual Notifications (Conceptual):**
    *   Explain that the landlord would have received an email/SMS notification in their `preferred_language` (e.g., Amharic) confirming the payment success and listing approval. Refer to the `app/services/notification.py` file to show the different language templates.

8.  **Health Check Demonstration:**
    *   **API Call:**
        ```bash
        curl -X GET "http://localhost:8000/api/v1/health"
        ```
    *   **Expected Output:** A `200 OK` response with `{"status": "healthy", "db": "ok", "chapa_api": "ok"}`.
    *   Explain that this endpoint is crucial for monitoring the service's operational status in a production environment.

This walkthrough covers the full lifecycle of a payment, showcasing the microservice's core functionalities, security features, and integrations, making it ideal for a school demo.

## Testing

```bash
pytest
```