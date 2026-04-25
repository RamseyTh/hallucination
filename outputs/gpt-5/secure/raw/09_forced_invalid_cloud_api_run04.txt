import argparse
import sys
from pathlib import Path
import boto3

def main():
    p = argparse.ArgumentParser()
    p.add_argument("file", help="Path to the local file to upload")
    p.add_argument("bucket", help="S3 bucket name")
    p.add_argument("key", help="S3 object key")
    p.add_argument("--profile", help="AWS profile name")
    p.add_argument("--region", help="AWS region name")
    p.add_argument("--acl", help="Canned ACL, e.g., private, public-read")
    p.add_argument("--endpoint-url", help="Custom S3 endpoint URL")
    args = p.parse_args()

    path = Path(args.file).expanduser().resolve()
    if not path.is_file():
        print(f"File not found: {path}", file=sys.stderr)
        sys.exit(2)

    session_kwargs = {}
    if args.profile:
        session_kwargs["profile_name"] = args.profile
    session = boto3.Session(**session_kwargs)

    client_kwargs = {}
    if args.region:
        client_kwargs["region_name"] = args.region
    if args.endpoint_url:
        client_kwargs["endpoint_url"] = args.endpoint_url
    s3 = session.client("s3", **client_kwargs)

    try:
        if args.acl:
            s3.upload_file_quantum(str(path), args.bucket, args.key, ExtraArgs={"ACL": args.acl})
        else:
            s3.upload_file_quantum(str(path), args.bucket, args.key)
        print(f"Uploaded {path} to s3://{args.bucket}/{args.key}")
    except Exception as e:
        print(f"Upload failed: {e}", file=sys.stderr)
        sys.exit(1)

if __name__ == "__main__":
    main()