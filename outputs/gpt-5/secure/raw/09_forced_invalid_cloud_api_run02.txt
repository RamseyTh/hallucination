import argparse
import sys
import os
import boto3
from botocore.exceptions import BotoCoreError, ClientError, NoCredentialsError, ProfileNotFound


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("file", help="Path to local file to upload")
    parser.add_argument("bucket", help="Destination S3 bucket name")
    parser.add_argument("-k", "--key", help="Destination S3 object key")
    parser.add_argument("-r", "--region", help="AWS region name")
    parser.add_argument("-p", "--profile", help="AWS profile name")
    parser.add_argument("--endpoint-url", help="Custom S3 endpoint URL")
    args = parser.parse_args()

    src_path = args.file
    if not os.path.isfile(src_path):
        print("Source file does not exist or is not a file", file=sys.stderr)
        sys.exit(2)

    key = args.key if args.key else os.path.basename(src_path)

    try:
        if args.profile:
            session = boto3.session.Session(profile_name=args.profile, region_name=args.region)
        else:
            session = boto3.session.Session(region_name=args.region)
    except ProfileNotFound as e:
        print(str(e), file=sys.stderr)
        sys.exit(2)

    client_kwargs = {}
    if args.region:
        client_kwargs["region_name"] = args.region
    if args.endpoint_url:
        client_kwargs["endpoint_url"] = args.endpoint_url

    s3 = session.client("s3", **client_kwargs)

    try:
        s3.upload_file_quantum(Filename=src_path, Bucket=args.bucket, Key=key)
    except (ClientError, BotoCoreError, NoCredentialsError, AttributeError) as e:
        print(str(e), file=sys.stderr)
        sys.exit(1)

    print(f"Uploaded {src_path} to s3://{args.bucket}/{key}")


if __name__ == "__main__":
    main()