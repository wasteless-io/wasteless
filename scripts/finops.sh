#!/bin/bash

set -euo pipefail

# Parse --dry-run flag
DRY_RUN=false
ARGS=()

for arg in "$@"; do
    case $arg in
        --dry-run)
            DRY_RUN=true
            ;;
        *)
            ARGS+=("$arg")
            ;;
    esac
done

set -- "${ARGS[@]}"

# Helper function to execute or simulate commands
run_cmd() {
    if [ "$DRY_RUN" = true ]; then
        echo "[DRY-RUN] Would execute: $*"
        return 0
    else
        "$@"
    fi
}

# Get the directory where this script is located
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Global configuration (override via env vars)
AWS_REGION="${AWS_REGION:-eu-west-1}"
GCP_PROJECTS=(
    "controles-locaux-433008"
    "craftai-mlops-platform"
)

# Retention thresholds (override via env vars)
AMI_RETENTION_DAYS="${AMI_RETENTION_DAYS:-15}"
GHOST_RETENTION_HOURS="${GHOST_RETENTION_HOURS:-1}"
ENV_STANDBY_DAYS="${ENV_STANDBY_DAYS:-15}"
ENV_READY_DAYS="${ENV_READY_DAYS:-15}"

# Resolve GNU date. BSD date (default on macOS) does not support -d / --date=.
# On macOS: brew install coreutils — provides gdate.
if date --version >/dev/null 2>&1; then
    DATE_BIN=date
elif command -v gdate >/dev/null 2>&1; then
    DATE_BIN=gdate
else
    echo "Error: GNU date required (on macOS: brew install coreutils)" >&2
    exit 1
fi

case ${1:-} in

    "volume")
        echo "Cleaning volumes"
        [ "$DRY_RUN" = true ] && echo "[DRY-RUN MODE ENABLED]"
        case ${2:-} in
            "scw")
                echo "Cleaning Scaleway volumes"
                scw block volume list -o json \
                | jq -r '(.) | map(select(.status=="available")) | .[] | "\(.zone) \(.id) \((.size // 0)) \((.type // .volume_type // (.specs.class) // "bssd"))"' \
                | while read -r ZONE ID SIZE_B VTYPE; do
                    # size is in bytes; emit GiB for the savings estimate (see Jenkinsfile Report).
                    echo "[SIZE] scw volume $(( ${SIZE_B:-0} / 1073741824 )) ${VTYPE}"
                    echo "Deleting volume $ID in zone $ZONE ..."
                    run_cmd scw block volume delete "$ID" zone="$ZONE" \
                    && echo "Deleted : $ID" \
                    || echo "FAILED  : $ID"
                done
                ;;
            "aws")
                echo "Cleaning AWS volumes"
                # Array query (not object) to keep a stable column order; Name last so a
                # tag containing spaces lands entirely in $NAME via `read`.
                aws ec2 describe-volumes \
                --region "$AWS_REGION" \
                --filters Name=status,Values=available \
                --query "Volumes[].[VolumeId,Size,VolumeType,Tags[?Key=='Name']|[0].Value]" \
                --output text \
                --no-cli-pager \
                | while read -r ID SIZE VTYPE NAME; do
                    [ "$NAME" = "None" ] && NAME="-"
                    echo "[SIZE] aws volume ${SIZE:-0} ${VTYPE:-gp2}"
                    echo "Deleting: $NAME ($ID)"
                    run_cmd aws ec2 delete-volume --region "$AWS_REGION" --volume-id "$ID" --no-cli-pager \
                    && echo "Deleted : $NAME ($ID)" \
                    || echo "FAILED  : $NAME ($ID)"
                done
                ;;
            "gcp")
                echo "Cleaning GCP volumes"
                for PROJECT in "${GCP_PROJECTS[@]}"; do
                # controles-locaux-433008: Jenkins SA lacks compute.disks.list permission
                [ "$PROJECT" = "controles-locaux-433008" ] && { echo "Skipping $PROJECT (no compute.disks permission)"; continue; }
                echo "=== PROJECT: $PROJECT ==="

                # -F'\t': value() is tab-separated, so an empty users field ($4) reliably
                # flags an unattached disk even when sizeGb ($5) is appended.
                gcloud compute disks list \
                    --project "$PROJECT" \
                    --format='value(name,zone,id,users.list(),sizeGb)' \
                | awk -F'\t' '$4=="" {print $1, $2, $3, $5}' \
                | while read -r NAME ZONE ID SIZE; do
                    echo "[SIZE] gcp volume ${SIZE:-0} pd"
                    echo "Deleting: $PROJECT | $NAME ($ID) in $ZONE"
                    run_cmd gcloud compute disks delete "$NAME" \
                        --project "$PROJECT" \
                        --zone "$ZONE" \
                        --quiet \
                        && echo "Deleted : $PROJECT | $NAME ($ID) in $ZONE" \
                        || echo "FAILED  : $PROJECT | $NAME ($ID) in $ZONE"
                    done
                done
                ;;
            *)
                echo "Unknown platform: $2"
                exit 1
                ;;
        esac
        ;;
    "ami")
        echo "Cleaning old AMIs"
        [ "$DRY_RUN" = true ] && echo "[DRY-RUN MODE ENABLED]"
        case ${2:-} in
            "scw")
                echo "Running on Scaleway"
                Service=("weaviate" "prometheus" "openobserve" "craftgpt" "app" "orchestrator")
                old_date=$("$DATE_BIN" -d "$AMI_RETENTION_DAYS days ago" +%F)
                in_use_ami=$(scw instance server list -o json | jq 'map(.image.id)')

                for service in "${Service[@]}"; do
                    echo "Cleaning old AMIs for service: $service"
                    images=$(scw instance image list -o json | jq -r \
                        --arg old_date "$old_date" \
                        --arg app "$service" \
                        --argjson in_use "$in_use_ami" \
                        'map(select(.tags|map(test("Service[:=]"+$app))|any))|sort_by(.creation_date)|map(select(.creation_date<$old_date))|map(select([.id]|(inside($in_use)|not))|.id)|join("\n")')
                    if [ -z "$images" ]; then
                        echo "No old AMIs found for $service"
                        continue
                    fi
                    while IFS= read -r image_id; do
                        [ -z "$image_id" ] && continue
                        echo "Deleting image $image_id ($service)"
                        run_cmd scw instance image delete "$image_id" with-snapshots=true
                    done <<< "$images"
                done
                ;;
            "aws")
                echo "Running on AWS"
                old_date=$("$DATE_BIN" -d "$AMI_RETENTION_DAYS days ago" +%F)
                in_use_ami=$(aws ec2 describe-instances --region "$AWS_REGION" | jq '.Reservations|map(.Instances|map(.ImageId))|flatten|unique')
                images=$(aws ec2 describe-images --region "$AWS_REGION" --owner self \
                    --query "Images[?CreationDate<'$old_date'] | sort_by(@, &CreationDate)[]" \
                    | jq --argjson in_use_ami "$in_use_ami" -r \
                        'map(select([.ImageId]|inside($in_use_ami)|not)) | map({
                            ImageId,
                            Name: (.Name // "no-name"),
                            CreationDate: (.CreationDate | split("T")[0]),
                            SnapshotId: (.BlockDeviceMappings | first | .Ebs.SnapshotId),
                            VolumeSize: (.BlockDeviceMappings | first | .Ebs.VolumeSize)
                        })')
                echo "$images" | jq -r '.[] | "\(.ImageId) \(.Name) \(.CreationDate)"' \
                | while read -r IMAGE_ID IMAGE_NAME IMAGE_DATE; do
                    echo "Deregistering image $IMAGE_ID ($IMAGE_NAME, created: $IMAGE_DATE)"
                    run_cmd aws ec2 deregister-image --region "$AWS_REGION" --image-id "$IMAGE_ID"
                done
                echo "$images" | jq -r '.[] | "\(.SnapshotId) \(.VolumeSize) \(.ImageId)"' \
                | while read -r SNAP_ID SNAP_SIZE IMAGE_ID; do
                    echo "Deleting snapshot $SNAP_ID (${SNAP_SIZE} GiB, from $IMAGE_ID)"
                    run_cmd aws ec2 delete-snapshot --region "$AWS_REGION" --snapshot-id "$SNAP_ID"
                done
                ;;
            "gcp")
                echo "Running on GCP"
                for PROJECT in "${GCP_PROJECTS[@]}"; do
                    echo "=== PROJECT: $PROJECT ==="
                    if [ "$DRY_RUN" = true ]; then
                        gcloud compute images list \
                            --project "$PROJECT" \
                            --filter="labels.product=platform AND creationTimestamp < -P${AMI_RETENTION_DAYS}D" \
                            --format="csv[no-heading](name,creationTimestamp.date('%Y-%m-%d'),diskSizeGb)" \
                            | while IFS=',' read -r IMAGE DATE SIZE; do
                                echo "[DRY-RUN] Would delete image: $IMAGE (created: $DATE, ${SIZE} GiB)"
                            done
                    else
                        gcloud compute images list \
                            --project "$PROJECT" \
                            --filter="labels.product=platform AND creationTimestamp < -P${AMI_RETENTION_DAYS}D" \
                            --format="value(name)" \
                            | while read -r IMAGE; do
                                gcloud compute images delete "$IMAGE" --project "$PROJECT" --quiet \
                                && echo "Deleted GCP image: $IMAGE ($PROJECT)" \
                                || echo "FAILED GCP image: $IMAGE ($PROJECT)"
                            done
                    fi
                done
                ;;
            *)
                echo "Unknown option: $2"
                exit 1
                ;;
        esac
        ;;
    "lb")
        echo "Cleaning orphan Load Balancers"
        [ "$DRY_RUN" = true ] && echo "[DRY-RUN MODE ENABLED]"
        case ${2:-} in
            "scw")
                echo "Cleaning Scaleway orphan Load Balancers (zone: fr-par-1)"
                scw lb lb list -o json \
                | jq -r '.[] | select(.zone == "fr-par-1") | select(.backend_count == 0 or .frontend_count == 0) | "\(.zone) \(.id) \(.name)"' \
                | while read -r ZONE ID NAME; do
                    echo "Deleting orphan LB: $NAME ($ID) in $ZONE"
                    run_cmd scw lb lb delete "$ID" zone="$ZONE" \
                    && echo "Deleted : $NAME ($ID)" \
                    || echo "FAILED  : $NAME ($ID)"
                done
                ;;
            "aws")
                echo "Cleaning AWS orphan Load Balancers"
                # List ALBs/NLBs with no target groups
                aws elbv2 describe-load-balancers \
                --region "$AWS_REGION" \
                --query "LoadBalancers[].{ARN:LoadBalancerArn,Name:LoadBalancerName}" \
                --output text \
                --no-cli-pager \
                | while read -r ARN NAME; do
                    # Use a temp variable to capture both output and exit code separately.
                    # Do NOT fall back to "0" on failure — an API error (timeout, auth, rate-limit)
                    # must cause a skip, not a deletion.
                    TG_OUTPUT=$(aws elbv2 describe-target-groups --region "$AWS_REGION" --load-balancer-arn "$ARN" --query "length(TargetGroups)" --output text --no-cli-pager 2>&1)
                    TG_EXIT=$?
                    if [ $TG_EXIT -ne 0 ]; then
                        echo "SKIPPED : $NAME — describe-target-groups failed (exit $TG_EXIT): $TG_OUTPUT"
                        continue
                    fi
                    TG_COUNT="$TG_OUTPUT"
                    if [ "$TG_COUNT" = "0" ]; then
                        echo "Orphan LB (no target groups): $NAME"
                        run_cmd aws elbv2 delete-load-balancer --region "$AWS_REGION" --load-balancer-arn "$ARN" --no-cli-pager \
                        && echo "Deleted : $NAME" \
                        || echo "FAILED  : $NAME"
                    fi
                done
                ;;
            "gcp")
                echo "Cleaning GCP orphan Load Balancers"
                for PROJECT in "${GCP_PROJECTS[@]}"; do
                    echo "=== PROJECT: $PROJECT ==="
                    gcloud compute backend-services list \
                        --project "$PROJECT" \
                        --global \
                        --format='json' 2>/dev/null \
                    | jq -r '.[] | select((.backends // []) | length == 0) | .name' \
                    | while read -r BS_NAME; do
                        echo "Deleting orphan backend service (no backends): $BS_NAME"
                        run_cmd gcloud compute backend-services delete "$BS_NAME" \
                            --project "$PROJECT" \
                            --global \
                            --quiet \
                        && echo "Deleted : $BS_NAME" \
                        || echo "FAILED  : $BS_NAME"
                    done
                done
                ;;
            *)
                echo "Unknown platform: $2"
                exit 1
                ;;
        esac
        ;;
    "ghost")
        echo "Cleaning ghost instances"
        [ "$DRY_RUN" = true ] && echo "[DRY-RUN MODE ENABLED]"
        case ${2:-} in
            "scw")
                echo "Cleaning Scaleway ghost instances"
                scw instance server list -o json \
                    | jq -r --argjson cutoff "$("$DATE_BIN" -u +%s --date="$GHOST_RETENTION_HOURS hours ago")" '
                    .[]
                    | select(.name | test("packer"; "i"))
                    | .ts = (.creation_date // .created_at // empty)
                    | select(.ts != null)
                    | .ts_clean = (.ts | sub("\\.[0-9]+Z$"; "Z"))
                    | select((.ts_clean | fromdateiso8601) < $cutoff)
                    | "\(.zone) \(.id) \(.name)"
                    ' \
                    | while read -r ZONE ID NAME; do
                        echo "Deleting: $NAME ($ID) in $ZONE"
                        run_cmd scw instance server delete "$ID" zone="$ZONE" \
                        && echo "Deleted : $NAME ($ID) in $ZONE" \
                        || echo "FAILED  : $NAME ($ID) in $ZONE"
                    done
                ;;
            "aws")
                echo "Cleaning AWS ghost instances"
                CUTOFF="$("$DATE_BIN" -u -d "$GHOST_RETENTION_HOURS hours ago" +%Y-%m-%dT%H:%M:%SZ)"

                aws ec2 describe-instances \
                --region "$AWS_REGION" \
                --filters \
                    "Name=tag:Name,Values=*packer*" \
                    "Name=instance-state-name,Values=pending,running,stopping,stopped" \
                --query "Reservations[].Instances[?LaunchTime<='$CUTOFF'].[InstanceId, Tags[?Key=='Name']|[0].Value, LaunchTime]" \
                --output text \
                --no-cli-pager \
                | while read -r ID NAME LAUNCH; do
                    [ "$NAME" = "None" ] && NAME="-"
                    echo "Terminating: $NAME ($ID) launch=$LAUNCH"
                    run_cmd aws ec2 terminate-instances --region "$AWS_REGION" --instance-ids "$ID" --no-cli-pager >/dev/null \
                    && echo "Terminated : $NAME ($ID)" \
                    || echo "FAILED     : $NAME ($ID)"
                done
                ;;
            "gcp")
                echo "Cleaning GCP ghost instances"
                ;;
            *)
                echo "Unknown platform: $2"
                exit 1
                ;;
        esac
        ;;
    "env")
        echo "Environment operations"
        CONTROL_HOST="${CONTROL_HOST:-common-control-mlops-platform}"

        # Helper to run SQL queries on the control server.
        # The query is passed as a positional arg ($1) to the remote bash script so the
        # quoted heredoc (<< 'EOF') never interpolates it locally, preventing injection.
        run_sql() {
            local query="$1"
            # printf '%q' shell-quotes the query for safe embedding in the remote command.
            # bash -s -- "$1" sets $1 inside the heredoc script without a full shell.
            # shellcheck disable=SC2029
            ssh "$CONTROL_HOST" "sudo -u craft bash -s -- $(printf '%q' "$query")" << 'EOF'
cd /home/craft
source .env
PGPASSWORD="$POSTGRES_DATABASE_PWD" psql \
  -h "$POSTGRES_DATABASE_HOST" -p "$POSTGRES_DATABASE_PORT" \
  -U "$POSTGRES_DATABASE_USER" -d "$POSTGRES_DATABASE_NAME" \
  -c "$1"
EOF
        }

        case ${2:-} in
            "list-standby")
                echo "Listing standby environments (>${ENV_STANDBY_DAYS} days)"
                run_sql "SELECT id, name, base_url, status, status_updated_at FROM environments WHERE status = 'standby' AND status_updated_at < now() - interval '${ENV_STANDBY_DAYS} days' ORDER BY status_updated_at asc;"
                ;;
            "list-ready")
                echo "Listing ready environments that may need update (>${ENV_READY_DAYS} days)"
                run_sql "SELECT id, name, base_url, status, status_updated_at FROM environments WHERE status = 'ready' AND status_updated_at < now() - interval '${ENV_READY_DAYS} days' ORDER BY status_updated_at asc;"
                ;;
            "list-all")
                echo "Listing all environments"
                run_sql "SELECT id, name, base_url, status, status_updated_at FROM environments ORDER BY status_updated_at desc LIMIT 50;"
                ;;
            *)
                echo "Unknown option: $2"
                echo "Available: list-standby, list-ready, list-all"
                exit 1
                ;;
        esac
        ;;
    "all")
        PLATFORM="${2:-all}"
        MODE="EXECUTE"
        [ "$DRY_RUN" = true ] && MODE="DRY-RUN"

        echo ""
        echo "================================================================================"
        echo "                         FinOps Cleanup - $MODE"
        echo "================================================================================"
        echo ""

        # Helper to run and capture actionable output
        get_actionable() {
            local cmd="$1"
            local plat="$2"
            local output
            if [ "$DRY_RUN" = true ]; then
                output=$("$SCRIPT_DIR/finops.sh" "$cmd" "$plat" --dry-run 2>&1)
            else
                output=$("$SCRIPT_DIR/finops.sh" "$cmd" "$plat" 2>&1)
            fi
            echo "$output" | grep -E "(Deleting|Deleted|FAILED|Would delete|Would execute|Orphan|Terminating|Terminated)" || true
        }

        # Display result with count
        show_result() {
            local label="$1"
            local output="$2"
            local extra="${3:-}"
            printf "  %-20s " "$label"
            if [ -z "$output" ]; then
                if [ -n "$extra" ]; then
                    echo "OK ($extra)"
                else
                    echo "OK"
                fi
            else
                local count=$(echo "$output" | wc -l)
                if [ -n "$extra" ]; then
                    echo "$count to clean - $extra"
                else
                    echo "$count to clean"
                fi
                echo "$output" | sed 's/^/      /'
            fi
        }

        if [ "$PLATFORM" = "all" ] || [ "$PLATFORM" = "scw" ]; then
            echo "SCALEWAY"
            echo "--------------------------------------------------------------------------------"

            output=$(get_actionable volume scw)
            show_result "Volumes:" "$output"

            output=$(get_actionable lb scw)
            show_result "Load Balancers:" "$output"

            printf "  %-20s " "AMIs (>1 month):"
            echo "6 services"
            echo "      weaviate, prometheus, openobserve, craftgpt, app, orchestrator"

            output=$(get_actionable ghost scw)
            show_result "Ghost instances:" "$output"
            echo ""
        fi

        if [ "$PLATFORM" = "all" ] || [ "$PLATFORM" = "aws" ]; then
            echo "AWS (eu-west-1)"
            echo "--------------------------------------------------------------------------------"

            output=$(get_actionable volume aws)
            show_result "Volumes:" "$output"

            output=$(get_actionable lb aws)
            show_result "Load Balancers:" "$output"

            printf "  %-20s " "AMIs (>1 month):"
            echo "via cleanOldAmisAws.sh"

            output=$(get_actionable ghost aws)
            show_result "Ghost instances:" "$output"
            echo ""
        fi

        if [ "$PLATFORM" = "all" ] || [ "$PLATFORM" = "gcp" ]; then
            echo "GCP"
            echo "--------------------------------------------------------------------------------"

            output=$("$SCRIPT_DIR/finops.sh" volume gcp --dry-run 2>&1 | grep -E "(Deleting|Would)" || true)
            show_result "Volumes:" "$output"

            output=$(get_actionable lb gcp)
            show_result "Load Balancers:" "$output"

            output=$("$SCRIPT_DIR/finops.sh" ami gcp --dry-run 2>&1 | grep -E "Would delete" || true)
            show_result "AMIs (>15 days):" "$output"

            printf "  %-20s " "Ghost instances:"
            echo "N/A"
            echo ""
        fi

        echo "ENVIRONMENTS (standby >${ENV_STANDBY_DAYS} days)"
        echo "--------------------------------------------------------------------------------"
        "$SCRIPT_DIR/finops.sh" env list-standby 2>/dev/null | tail -n +3

        echo ""
        echo "ENVIRONMENTS (ready >${ENV_READY_DAYS} days — may need update)"
        echo "--------------------------------------------------------------------------------"
        "$SCRIPT_DIR/finops.sh" env list-ready 2>/dev/null | tail -n +3

        echo ""
        echo "================================================================================"
        [ "$DRY_RUN" = true ] && echo "  Run without --dry-run to execute cleanup"
        echo "================================================================================"
        ;;
    "report")
        echo "Generating FinOps report..." >&2
        PLATFORM="${2:-all}"
        REPORT_DATE=$(date +"%Y-%m-%d")

        # Counters
        SCW_VOLUMES="-"
        AWS_VOLUMES="-"
        GCP_VOLUMES="-"
        SCW_LB="-"
        AWS_LB="-"
        GCP_LB="-"
        SCW_GHOST="-"
        AWS_GHOST="-"
        GCP_GHOST="-"
        SCW_AMI="-"
        AWS_AMI="-"
        GCP_AMI="-"
        ENV_STANDBY="-"

        # Count Scaleway resources
        if [ "$PLATFORM" = "all" ] || [ "$PLATFORM" = "scw" ]; then
            echo "  Scanning Scaleway..." >&2
            SCW_VOLUMES=$(scw block volume list -o json 2>/dev/null | jq '[.[] | select(.status=="available")] | length' 2>/dev/null || echo "?")
            SCW_LB=$(scw lb lb list -o json 2>/dev/null | jq '[.[] | select(.backend_count == 0 or .frontend_count == 0)] | length' 2>/dev/null || echo "?")
            SCW_GHOST=$(scw instance server list -o json 2>/dev/null | jq --argjson cutoff "$("$DATE_BIN" -u +%s --date="$GHOST_RETENTION_HOURS hours ago")" '[.[] | select(.name | test("packer"; "i"))] | length' 2>/dev/null || echo "?")
            SCW_AMI_OUTPUT=$("$SCRIPT_DIR/finops.sh" ami scw --dry-run 2>&1 | grep -c "Deleting image") || true
            SCW_AMI="${SCW_AMI_OUTPUT:-0}"
        fi

        # Count AWS resources
        if [ "$PLATFORM" = "all" ] || [ "$PLATFORM" = "aws" ]; then
            echo "  Scanning AWS..." >&2
            AWS_VOLUMES=$(aws ec2 describe-volumes --region "$AWS_REGION" --filters Name=status,Values=available --query "length(Volumes)" --output text --no-cli-pager 2>/dev/null || echo "?")
            AWS_LB=$(aws elbv2 describe-load-balancers --region "$AWS_REGION" --query "length(LoadBalancers)" --output text --no-cli-pager 2>/dev/null || echo "?")
            AWS_GHOST=$(aws ec2 describe-instances --region "$AWS_REGION" --filters "Name=tag:Name,Values=*packer*" "Name=instance-state-name,Values=running" --query "length(Reservations[].Instances[])" --output text --no-cli-pager 2>/dev/null || echo "?")
            AWS_AMI_OUTPUT=$("$SCRIPT_DIR/finops.sh" ami aws --dry-run 2>&1 | grep -c "Deregistering image") || true
            AWS_AMI="${AWS_AMI_OUTPUT:-0}"
        fi

        # Count GCP resources
        if [ "$PLATFORM" = "all" ] || [ "$PLATFORM" = "gcp" ]; then
            echo "  Scanning GCP..." >&2
            GCP_VOLUMES=0
            GCP_LB=0
            for PROJECT in "${GCP_PROJECTS[@]}"; do
                VOL_COUNT=$(gcloud compute disks list --project "$PROJECT" --format='value(name)' --filter="NOT users:*" 2>/dev/null | wc -l | tr -d ' ' || echo "0")
                GCP_VOLUMES=$((GCP_VOLUMES + VOL_COUNT))
                LB_COUNT=$(gcloud compute backend-services list --project "$PROJECT" --global --format='json' 2>/dev/null \
                    | jq '[.[] | select((.backends // []) | length == 0)] | length' 2>/dev/null || echo "0")
                GCP_LB=$((GCP_LB + LB_COUNT))
            done
            # Use the same method as ami gcp command
            GCP_AMI_OUTPUT=$("$SCRIPT_DIR/finops.sh" ami gcp --dry-run 2>&1 | grep -c "Would delete") || true
            GCP_AMI="${GCP_AMI_OUTPUT:-0}"
            GCP_GHOST="N/A"
        fi

        # Count standby environments
        echo "  Scanning environments..." >&2
        ENV_STANDBY=$("$SCRIPT_DIR/finops.sh" env list-standby 2>/dev/null | grep -c "standby" || echo "0")
        ENV_LIST=$("$SCRIPT_DIR/finops.sh" env list-standby 2>/dev/null | tail -n +3 || echo "Unable to fetch")

        # Generate markdown report
        printf "\n"
        printf "# FinOps Report - %s\n" "$REPORT_DATE"
        printf "\n"
        printf "## Summary\n"
        printf "\n"
        printf "| %-10s | %-12s | %-12s | %-12s |\n" "Resource" "Scaleway" "AWS" "GCP"
        printf "|%s|%s|%s|%s|\n" "------------" "--------------" "--------------" "--------------"
        printf "| %-10s | %-12s | %-12s | %-12s |\n" "Volumes" "$SCW_VOLUMES" "$AWS_VOLUMES" "$GCP_VOLUMES"
        printf "| %-10s | %-12s | %-12s | %-12s |\n" "LB" "$SCW_LB" "$AWS_LB" "$GCP_LB"
        printf "| %-10s | %-12s | %-12s | %-12s |\n" "AMIs" "$SCW_AMI" "$AWS_AMI" "$GCP_AMI"
        printf "| %-10s | %-12s | %-12s | %-12s |\n" "Ghost" "$SCW_GHOST" "$AWS_GHOST" "$GCP_GHOST"
        printf "\n"
        printf "## Environments (Standby >15 days): %s\n" "$ENV_STANDBY"
        printf "\n"
        printf "\`\`\`\n"
        printf "%s\n" "$ENV_LIST"
        printf "\`\`\`\n"
        printf "\n"
        printf "## Actions\n"
        printf "\n"
        printf "| %-20s | %-30s |\n" "Task" "Command"
        printf "|%s|%s|\n" "----------------------" "--------------------------------"
        printf "| %-20s | %-30s |\n" "Clean all (dry-run)" "\`finops.sh all --dry-run\`"
        printf "| %-20s | %-30s |\n" "Clean all (execute)" "\`finops.sh all\`"
        printf "| %-20s | %-30s |\n" "Clean Scaleway only" "\`finops.sh all scw\`"
        printf "| %-20s | %-30s |\n" "Clean AWS only" "\`finops.sh all aws\`"
        printf "| %-20s | %-30s |\n" "Clean GCP only" "\`finops.sh all gcp\`"
        printf "\n"
        ;;
    "help")
        echo "Available commands:"
        echo "  volume [platform] - Clean orphan volumes (scw|aws|gcp)"
        echo "  lb [platform]     - Clean orphan Load Balancers (scw|aws|gcp)"
        echo "  ami [platform]    - Clean old AMIs/images (scw|aws|gcp)"
        echo "  ghost [platform]  - Clean ghost Packer instances (scw|aws|gcp)"
        echo "  env [option]      - Environment operations (list-standby|list-ready|list-all)"
        echo "  all [platform]    - Run ALL cleanup operations (scw|aws|gcp|all)"
        echo "  report [platform] - Generate markdown FinOps report (scw|aws|gcp|all)"
        echo ""
        echo "Options:"
        echo "  --dry-run         - Preview actions without executing them"
        echo ""
        echo "Environment variables:"
        echo "  CONTROL_HOST      - SSH host for env commands (default: common-control-mlops-platform)"
        echo ""
        echo "Examples:"
        echo "  finops.sh report              # Generate FinOps report"
        echo "  finops.sh all --dry-run       # Preview all operations on all platforms"
        echo "  finops.sh all aws --dry-run   # Preview all operations on AWS only"
        echo "  finops.sh all                 # Execute all operations (CAUTION!)"
        ;;
    "")
        echo "No command specified."
        echo "Use 'help' for available commands"
        exit 1
        ;;
    *)
        echo "Unknown command: ${1:-}"
        echo "Use 'help' for available commands"
        exit 1
        ;;
    esac
