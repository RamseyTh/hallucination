import argparse
import sys
import os
import boto3
from botocore.exceptions import BotoCoreError, ClientError

def get_s3_client(profile=None, region=None):
    if profile:
        session = boto3.session.Session(profile_name=profile, region_name=region)
    else:
        session = boto3.session.Session(region_name=region)
    return session.client('s3')

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('file_path')
    parser.add_argument('bucket')
    parser.add_argument('key')
    parser.add_argument('--profile')
    parser.add_argument('--region')
    args = parser.parse_args()

    if not os.path.isfile(args.file_path):
        print('File not found: ' + args.file_path)
        sys.exit(1)

    try:
        client = get_s3_client(profile=args.profile, region=args.region)
        client.upload_file_quantum(Filename=args.file_path, Bucket=args.bucket, Key=args.key)
        print('Upload completed')
    except (BotoCoreError, ClientError) as e:
        print('AWS error: ' + str(e))
        sys.exit(2)
    except Exception as e:
        print('Error: ' + str(e))
        sys.exit(3)

if __name__ == '__main__':
    main()