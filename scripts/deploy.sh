#!/usr/bin/env bash
# Build, deploy the SAM stack, publish public/ to the site bucket, and print the URLs.
# Needs AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY / AWS_DEFAULT_REGION in the environment.
set -euo pipefail
cd "$(dirname "$0")/.."
STACK="${STACK:-foodbank-spatial-twin}"
REGION="${AWS_DEFAULT_REGION:-us-east-1}"

sam validate --lint
sam build
sam deploy --stack-name "$STACK" --region "$REGION" --resolve-s3 \
  --capabilities CAPABILITY_IAM --no-confirm-changeset --no-fail-on-empty-changeset

out() { aws cloudformation describe-stacks --stack-name "$STACK" --region "$REGION" \
  --query "Stacks[0].Outputs[?OutputKey=='$1'].OutputValue" --output text; }
BUCKET="$(out SiteBucketName)"; API="$(out ApiBaseUrl)"

# Inject the API base URL into the placeholder page, then sync (never mutate the source file).
STAGE="$(mktemp -d)"; cp -r public/. "$STAGE"/
sed -i "s#window.TWIN_API || \"\"#window.TWIN_API || \"$API\"#" "$STAGE/index.html"
aws s3 sync "$STAGE" "s3://$BUCKET" --delete --region "$REGION"
rm -rf "$STAGE"

echo "Telemetry: $(out TelemetryUrl)"
echo "Site:      $(out SiteUrl)"
