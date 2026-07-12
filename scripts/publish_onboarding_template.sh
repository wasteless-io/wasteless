#!/bin/bash
#
# Publie le template CloudFormation d'onboarding sur le bucket S3 public
# qui alimente le lien "quick-create" de la page /setup.
#
# Usage: ./scripts/publish_onboarding_template.sh [version]
#   version   Prefixe de publication versionne (ex: v0.1.0). Le template est
#             toujours publie sous latest/ (l'URL par defaut de /setup) et,
#             si une version est donnee, sous <version>/ egalement.
#
# Prerequis: AWS CLI configure avec des credentials capables de creer le
# bucket et d'ecrire dedans (compte wasteless-io). A lancer une fois pour
# creer le bucket, puis a chaque modification du template.
#
set -euo pipefail

BUCKET="${WASTELESS_ONBOARDING_BUCKET:-wasteless-io-onboarding}"
REGION="${WASTELESS_ONBOARDING_BUCKET_REGION:-eu-west-1}"
VERSION="${1:-}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TEMPLATE="$SCRIPT_DIR/../onboarding/cloudformation/wasteless-onboarding.yaml"

if [ ! -f "$TEMPLATE" ]; then
    echo "[ERROR] Template introuvable: $TEMPLATE" >&2
    exit 1
fi

# Le template doit etre du CloudFormation valide avant d'etre publie.
aws cloudformation validate-template --template-body "file://$TEMPLATE" \
    --region "$REGION" > /dev/null
echo "[OK] Template valide par CloudFormation"

# Creation du bucket si absent (idempotent).
if ! aws s3api head-bucket --bucket "$BUCKET" 2>/dev/null; then
    echo "[INFO] Creation du bucket $BUCKET ($REGION)..."
    aws s3api create-bucket \
        --bucket "$BUCKET" \
        --region "$REGION" \
        --create-bucket-configuration "LocationConstraint=$REGION"
    # Lecture publique par policy uniquement (pas d'ACL) : on debloque
    # BlockPublicPolicy/RestrictPublicBuckets, les ACLs restent bloquees.
    aws s3api put-public-access-block \
        --bucket "$BUCKET" \
        --public-access-block-configuration \
        "BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=false,RestrictPublicBuckets=false"
    aws s3api put-bucket-policy --bucket "$BUCKET" --policy "{
        \"Version\": \"2012-10-17\",
        \"Statement\": [{
            \"Sid\": \"PublicReadOnboardingTemplate\",
            \"Effect\": \"Allow\",
            \"Principal\": \"*\",
            \"Action\": \"s3:GetObject\",
            \"Resource\": \"arn:aws:s3:::$BUCKET/*\"
        }]
    }"
    echo "[OK] Bucket cree avec lecture publique (GetObject uniquement)"
else
    echo "[OK] Bucket $BUCKET existant"
fi

aws s3 cp "$TEMPLATE" "s3://$BUCKET/latest/wasteless-onboarding.yaml" --region "$REGION"
echo "[OK] Publie: https://$BUCKET.s3.$REGION.amazonaws.com/latest/wasteless-onboarding.yaml"

if [ -n "$VERSION" ]; then
    aws s3 cp "$TEMPLATE" "s3://$BUCKET/$VERSION/wasteless-onboarding.yaml" --region "$REGION"
    echo "[OK] Publie: https://$BUCKET.s3.$REGION.amazonaws.com/$VERSION/wasteless-onboarding.yaml"
fi
