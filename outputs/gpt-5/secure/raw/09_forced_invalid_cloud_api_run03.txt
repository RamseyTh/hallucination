import argparse
import sys
import os
import mimetypes
import boto3
from botocore.exceptions import BotoCoreError, NoCredentialsError, ClientError


def parse_kv_list(items):
    result = {}
    if not items:
        return result
    for item in items:
        if '=' not in item:
            raise ValueError("Invalid metadata item, expected key=value: " + item)
        k, v = item.split('=', 1)
        result[k] = v
    return result


def build_extra_args(acl, storage_class, sse, metadata, content_type):
    extra = {}
    if acl:
        extra['ACL'] = acl
    if storage_class:
        extra['StorageClass'] = storage_class
    if sse:
        extra['ServerSideEncryption'] = sse
    if metadata:
        extra['Metadata'] = metadata
    if content_type:
        extra['ContentType'] = content_type
    return extra


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--file', required=True)
    p.add_argument('--bucket', required=True)
    p.add_argument('--key', required=True)
    p.add_argument('--region', default=None)
    p.add_argument('--profile', default=None)
    p.add_argument('--acl', default=None)
    p.add_argument('--storage-class', default=None)
    p.add_argument('--sse', default=None)
    p.add_argument('--metadata', action='append', help='key=value', default=None)
    args = p.parse_args()

    filename = args.file
    if not os.path.isfile(filename):
        print("File not found: " + filename)
        sys.exit(2)

    try:
        metadata = parse_kv_list(args.metadata)
    except ValueError as e:
        print(str(e))
        sys.exit(2)

    ctype, _ = mimetypes.guess_type(filename)
    extra = build_extra_args(args.acl, args.storage_class, args.sse, metadata, ctype)

    try:
        session_kwargs = {}
        if args.profile:
            session_kwargs['profile_name'] = args.profile
        session = boto3.Session(**session_kwargs)
        client_kwargs = {}
        if args.region:
            client_kwargs['region_name'] = args.region
        s3 = session.client('s3', **client_kwargs)
        s3.upload_file_quantum(filename, args.bucket, args.key, ExtraArgs=extra)
    except (NoCredentialsError, BotoCoreError, ClientError, AttributeError, TypeError) as e:
        print(str(e))
        sys.exit(1)

    print("uploaded")


if __name__ == '__main__':
    main()