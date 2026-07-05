#!/usr/bin/env bash
# run-jenkins-pipeline.sh — trigger the Android APK build and stream the log.
#
# Usage:
#   ./test_scripts/run-jenkins-pipeline.sh
#
# Requires:
#   - Jenkins running (docker compose up -d jenkins)
#   - ~/.jenkins-token containing your API token
#     echo "your-token" > ~/.jenkins-token && chmod 600 ~/.jenkins-token

set -euo pipefail

JENKINS_URL="http://192.168.86.153:8888"
JENKINS_USER="kavi741"
JENKINS_TOKEN_FILE="$HOME/.jenkins-token"
JOB_NAME="xteink-commonplace-android"
CLI_JAR="/tmp/jenkins-cli.jar"

# ---- Preflight checks -------------------------------------------------------

if [ ! -f "$JENKINS_TOKEN_FILE" ]; then
    echo "ERROR: $JENKINS_TOKEN_FILE not found."
    echo "  Generate a token: Jenkins UI → User menu → Security → Add new token"
    echo "  Then: echo 'your-token' > $JENKINS_TOKEN_FILE && chmod 600 $JENKINS_TOKEN_FILE"
    exit 1
fi

TOKEN=$(cat "$JENKINS_TOKEN_FILE")

# Download CLI jar into container if not already there
docker exec jenkins bash -c "
    [ -f $CLI_JAR ] || curl -s -o $CLI_JAR $JENKINS_URL/jnlpJars/jenkins-cli.jar
"

cli() {
    docker exec jenkins java -jar "$CLI_JAR" \
        -s "$JENKINS_URL" -webSocket \
        -auth "$JENKINS_USER:$TOKEN" \
        "$@"
}

# ---- Trigger build and stream log -------------------------------------------

echo "Triggering Jenkins pipeline: $JOB_NAME"
echo "Jenkins URL: $JENKINS_URL"
echo ""

# -s = wait for completion, -v = stream console log
cli build "$JOB_NAME" -s -v
STATUS=$?

echo ""
if [ $STATUS -eq 0 ]; then
    echo "BUILD SUCCESS"
    echo "Download APK from: $JENKINS_URL/job/$JOB_NAME/lastSuccessfulBuild/artifact/"
else
    echo "BUILD FAILED (exit $STATUS)"
    echo "Full log: $JENKINS_URL/job/$JOB_NAME/lastBuild/console"
    exit $STATUS
fi
