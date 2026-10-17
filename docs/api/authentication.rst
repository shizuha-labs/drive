Authentication
==============

Shizuha Drive uses JWT (JSON Web Token) authentication via Shizuha ID.

Obtaining a Token
-----------------

Authenticate with Shizuha ID to obtain a JWT token:

.. http:post:: /shizuha-id/api/auth/login/

   Authenticate and receive JWT tokens.

   **Request:**

   .. code-block:: json

      {
        "email": "user@example.com",
        "password": "your-password"
      }

   **Response:**

   .. code-block:: json

      {
        "access": "eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzI1NiJ9...",
        "refresh": "eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzI1NiJ9...",
        "user": {
          "id": 1,
          "email": "user@example.com",
          "name": "John Doe"
        }
      }

Using the Token
---------------

Include the access token in the ``Authorization`` header for all API requests:

.. code-block:: bash

   curl -X GET https://your-domain/shizuha-drive/api/files/ \
     -H "Authorization: Bearer eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzI1NiJ9..."

Token Refresh
-------------

Access tokens expire after 1 hour. Use the refresh token to obtain a new access token:

.. http:post:: /shizuha-id/api/auth/refresh/

   Refresh the access token.

   **Request:**

   .. code-block:: json

      {
        "refresh": "eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzI1NiJ9..."
      }

   **Response:**

   .. code-block:: json

      {
        "access": "eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzI1NiJ9..."
      }

Token Expiration
----------------

.. list-table::
   :header-rows: 1
   :widths: 30 70

   * - Token Type
     - Expiration
   * - Access Token
     - 1 hour
   * - Refresh Token
     - 7 days

Service Accounts
----------------

For automated integrations, request a service account token from your administrator.
Service account tokens have longer expiration times and can be scoped to specific operations.

Error Responses
---------------

.. http:any:: /api/*

   :statuscode 401: Token is missing, expired, or invalid

   **Response:**

   .. code-block:: json

      {
        "detail": "Given token not valid for any token type",
        "code": "token_not_valid",
        "messages": [
          {
            "token_class": "AccessToken",
            "token_type": "access",
            "message": "Token is invalid or expired"
          }
        ]
      }
