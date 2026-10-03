#!/bin/sh
# Generates a JWT signing key on first run, in local/test only: replicas each
# inventing one would reject each other's tokens. Kept on a shared volume.
set -e

SECRET_FILE="${JWT_SECRET_FILE:-/var/lib/synapse/jwt_secret}"

if [ -z "${JWT_SECRET_KEY}" ]; then
    case "${ENVIRONMENT:-local}" in
        local | test)
            if [ ! -f "${SECRET_FILE}" ]; then
                mkdir -p "$(dirname "${SECRET_FILE}")"
                python -c "import secrets; print(secrets.token_urlsafe(48))" \
                    > "${SECRET_FILE}"
                chmod 600 "${SECRET_FILE}"
                echo "entrypoint: generated a development signing key at ${SECRET_FILE}" >&2
            fi
            JWT_SECRET_KEY="$(cat "${SECRET_FILE}")"
            export JWT_SECRET_KEY
            ;;
        *)
            echo "entrypoint: JWT_SECRET_KEY is required when ENVIRONMENT=${ENVIRONMENT}." >&2
            echo "entrypoint: it is generated automatically only for local/test, because" >&2
            echo "entrypoint: replicas each generating their own key would reject each" >&2
            echo "entrypoint: other's tokens. Supply it from your secret store." >&2
            exit 1
            ;;
    esac
fi

exec "$@"
