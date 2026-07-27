import boto3
import os
from datetime import datetime, timezone

def lambda_handler(event, context):
    ec2 = boto3.client('ec2')
    sns = boto3.client('sns')

    # Environment Variables
    dry_run = os.environ.get("DRY_RUN", "true").lower() == "true"
    age_threshold_days = int(os.environ.get("AGE_THRESHOLD_DAYS", "30"))
    sns_topic_arn = os.environ.get("SNS_TOPIC_ARN")
    cost_per_gb = float(os.environ.get("COST_PER_GB", "0.05"))

    print(f"DRY_RUN Mode: {dry_run}")
    print(f"Age Threshold: {age_threshold_days} days")

    # Get all snapshots owned by this AWS account
    response = ec2.describe_snapshots(OwnerIds=['self'])

    total_snapshots = len(response.get("Snapshots", []))
    deleted_count = 0
    skipped_count = 0
    total_deleted_size_gb = 0

    # Process each snapshot
    for snapshot in response.get("Snapshots", []):

        snapshot_id = snapshot["SnapshotId"]
        volume_id = snapshot.get("VolumeId")
        snapshot_size = snapshot.get("VolumeSize", 0)

        # Calculate snapshot age
        snapshot_age = (
            datetime.now(timezone.utc) - snapshot["StartTime"]
        ).days

        print(f"Snapshot {snapshot_id} is {snapshot_age} days old.")

        # Skip if snapshot is newer than threshold
        if snapshot_age < age_threshold_days:
            skipped_count += 1

            print("=" * 60)
            print(f"Snapshot ID  : {snapshot_id}")
            print(f"Age          : {snapshot_age} days")
            print("Action       : SKIPPED")
            print(f"Reason       : Younger than {age_threshold_days} days")
            print("=" * 60)

            continue

        # Check for AutoCleanup tag
        tags = snapshot.get("Tags", [])

        auto_cleanup = any(
            tag["Key"] == "AutoCleanup" and
            tag["Value"].lower() == "true"
            for tag in tags
        )

        if not auto_cleanup:
            skipped_count += 1
            print(f"Skipping {snapshot_id} (Missing AutoCleanup=true tag)")
            continue

        # Snapshot has no associated volume
        if not volume_id:

            if dry_run:
                print(f"[DRY RUN] Would delete snapshot {snapshot_id} (No associated volume).")
            else:
                ec2.delete_snapshot(SnapshotId=snapshot_id)
                print(f"Deleted snapshot {snapshot_id} (No associated volume).")

            deleted_count += 1
            total_deleted_size_gb += snapshot_size

        else:

            try:
                volume_response = ec2.describe_volumes(
                    VolumeIds=[volume_id]
                )

                attachments = volume_response["Volumes"][0]["Attachments"]

                if not attachments:

                    if dry_run:
                        print(f"[DRY RUN] Would delete snapshot {snapshot_id} (Volume not attached).")
                    else:
                        ec2.delete_snapshot(SnapshotId=snapshot_id)
                        print(f"Deleted snapshot {snapshot_id} (Volume not attached).")

                    deleted_count += 1
                    total_deleted_size_gb += snapshot_size

            except ec2.exceptions.ClientError as e:

                if e.response["Error"]["Code"] == "InvalidVolume.NotFound":

                    if dry_run:
                        print(f"[DRY RUN] Would delete snapshot {snapshot_id} (Volume not found).")
                    else:
                        ec2.delete_snapshot(SnapshotId=snapshot_id)
                        print(f"Deleted snapshot {snapshot_id} (Volume not found).")

                    deleted_count += 1
                    total_deleted_size_gb += snapshot_size

                else:
                    raise

    # Estimate monthly savings
    estimated_monthly_savings = total_deleted_size_gb * cost_per_gb

    # Build email message
    message = f"""
EBS Snapshot Cleanup Summary

Execution Status : SUCCESS

Total Snapshots Scanned : {total_snapshots}

Snapshots Deleted/Would Delete : {deleted_count}

Snapshots Skipped : {skipped_count}

Estimated Storage Removed : {total_deleted_size_gb} GB

Estimated Monthly Savings : ${estimated_monthly_savings:.2f}

DRY_RUN : {dry_run}

Age Threshold : {age_threshold_days} days
"""

    # Send SNS notification
    if sns_topic_arn:
        sns.publish(
            TopicArn=sns_topic_arn,
            Subject="EBS Snapshot Cleanup Report",
            Message=message
        )

        print("SNS notification sent successfully.")

    print(f"Estimated Storage Removed: {total_deleted_size_gb} GB")
    print(f"Estimated Monthly Savings: ${estimated_monthly_savings:.2f}")

    return {
        "statusCode": 200,
        "body": {
            "TotalSnapshots": total_snapshots,
            "Deleted": deleted_count,
            "Skipped": skipped_count,
            "EstimatedSavings": round(estimated_monthly_savings, 2)
        }
    }
