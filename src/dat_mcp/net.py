"""Settings every service client uses for outbound HTTPS."""

import ssl

import truststore

# Check certificates against the operating system's store. On networks that
# inspect TLS (HRSA's does for api.usaspending.gov and FAC's PDF host), the
# network's root certificate is installed there, so verification stays on.
SSL_CONTEXT = truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
