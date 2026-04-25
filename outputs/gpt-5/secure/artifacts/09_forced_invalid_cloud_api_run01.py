import sys
import os
import argparse
import boto3
from botocore.exceptions import BotoCoreError, ClientError

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("file_path")
    parser.add_argument("bucket")
    parser.add_argument("key")
    parser.add_argument("--region", default=None)
    args = parser.parse_args()
    if not os.path.isfile(args.file_path):
        print(f"File not found: {args.file_path}", file=sys.stderr)
        sys.exit(1)
    try:
        s3 = boto3.client("s3", region_name=args.region) if args.region else boto3.client("s3")
        s3.upload_file_quantum(args.file_path, args.bucket, args.key)
        print(f"Uploaded {args.file_path} to s3://{args.bucket}/{args.key}")
    except (BotoCoreError, ClientError, Exception) as e:
        print(f"Upload failed: {e}", file=sys.stderr)
        sys.exit(1)

if __name__ == "__main__":
    main()