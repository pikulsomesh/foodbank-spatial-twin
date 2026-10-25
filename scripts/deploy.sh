#!/usr/bin/env bash
# Build, deploy the SAM stack, publish public/ to the site bucket, and print the URLs.
# Needs AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY / AWS_DEFAULT_REGION in the environment.
set -euo pipefail
cd "$(dirname "$0")/.."
STACK="${STACK:-foodbank-spatial-twin}"
# us-east-2: the org SCP on Root (region restriction) blocks Lambda, CloudFormation and API Gateway in us-east-1 and us-west-2.
REGION="${AWS_DEFAULT_REGION:-us-east-2}"

sam validate --lint
sam build
sam deploy --stack-name "$STACK" --region "$REGION" --resolve-s3 \
  --capabilities CAPABILITY_IAM --no-confirm-changeset --no-fail-on-empty-changeset

out() { aws cloudformation describe-stacks --stack-name "$STACK" --region "$REGION" \
  --query "Stacks[0].Outputs[?OutputKey=='$1'].OutputValue" --output text; }
BUCKET="$(out SiteBucketName)"; API="$(out ApiBaseUrl)"

# Point the viewer at the API in a staged copy, then sync (never mutate the source file).
STAGE="$(mktemp -d)"; cp -r public/. "$STAGE"/
python3 - "$STAGE/twin.config.json" "$API" <<'PY'
import json, sys
path, api = sys.argv[1], sys.argv[2]
cfg = json.load(open(path)); cfg["telemetryApi"] = api
json.dump(cfg, open(path, "w"), indent=2)
PY
aws s3 sync "$STAGE" "s3://$BUCKET" --delete --region "$REGION"
rm -rf "$STAGE"

echo "Telemetry: $(out TelemetryUrl)"
echo "Site:      $(out SiteUrl)"

# Point the GitHub Pages demo at the API too: set the repo variable the Pages workflow reads,
# then redeploy. Needs an authenticated gh CLI; otherwise print the two steps.
REPO="${GITHUB_REPO:-pikulsomesh/foodbank-spatial-twin}"
if command -v gh >/dev/null && gh auth status >/dev/null 2>&1; then
  gh variable set TWIN_API_URL --repo "$REPO" --body "$API"
  gh workflow run pages.yml --repo "$REPO" --ref main
  echo "Pages:     redeploying https://${REPO%%/*}.github.io/${REPO#*/}/ with live data"
else
  echo "Pages:     set repo variable TWIN_API_URL=$API (Settings > Secrets and variables > Actions > Variables),"
  echo "           then run the 'Demo site' workflow on main."
fi
