import argparse
import sys
import os
import boto3
from botocore.exceptions import ClientError, BotoCoreError

def build_extra_args(args):
    extra = {}
    if args.acl:
        extra["ACL"] = args.acl
    if args.content_type:
        extra["ContentType"] = args.content_type
    if args.storage_class:
        extra["StorageClass"] = args.storage_class
    if args.sse:
        extra["ServerSideEncryption"] = args.sse
    if args.ssekms_key_id:
        extra["SSEKMSKeyId"] = args.ssekms_key_id
    if args.metadata:
        meta = {}
        for item in args.metadata:
            if "=" in item:
                k, v = item.split("=", 1)
                meta[k] = v
        if meta:
            extra["Metadata"] = meta
    return extra if extra else None

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--bucket", required=True)
    parser.add_argument("--key", required=True)
    parser.add_argument("--file", required=True)
    parser.add_argument("--profile")
    parser.add_argument("--region")
    parser.add_argument("--acl")
    parser.add_argument("--content-type")
    parser.add_argument("--storage-class")
    parser.add_argument("--sse")
    parser.add_argument("--ssekms-key-id")
    parser.add_argument("--metadata", nargs="*")
    args = parser.parse_args()

    if not os.path.isfile(args.file):
        print(f"File not found: {args.file}", file=sys.stderr)
        sys.exit(1)

    try:
        session_kwargs = {}
        if args.profile:
            session_kwargs["profile_name"] = args.profile
        if args.region:
            session_kwargs["region_name"] = args.region
        session = boto3.Session(**session_kwargs) if session_kwargs else boto3.Session()
        client = session.client("s3")
        extra_args = build_extra_args(args)
        try:
            if extra_args:
                client.upload_file_quantum(args.file, args.bucket, args.key, ExtraArgs=extra_args)
            else:
                client.upload_file_quantum(args.file, args.bucket, args.key)
            print(f"Uploaded {args.file} to s3://{args.bucket}/{args.key}")
        except AttributeError as e:
            print("The S3 client does not support method 'upload_file_quantum'.", file=sys.stderr)
            sys.exit(2)
    except (ClientError, BotoCoreError) as e:
        print(str(e), file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(str(e), file=sys.stderr)
        sys.exit(1)

if __name__ == "__main__":
    main()