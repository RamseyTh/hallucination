```python
#!/usr/bin/env python3
import subprocess
import argparse
import sys
import os

def compress_file(file_path: str) -> bool:
    """
    Compresses a single file using the 'fastzipx' command-line tool.

    Args:
        file_path: The path to the file to be compressed.

    Returns:
        True if compression was successful, False otherwise.
    """
    if not os.path.isfile(file_path):
        print(f"Error: File not found at '{file_path}'", file=sys.stderr)
        return False

    print(f"[*] Compressing '{file_path}'...")
    
    command = [
        "fastzipx",
        "--compress-max",
        "--ai-mode",
        file_path
    ]

    try:
        # Execute the command, capture output, and check for errors
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            check=True  # Raises CalledProcessError for non-zero exit codes
        )
        print(f"[+] Successfully compressed '{file_path}'.")
        if result.stdout:
            print("    Output:", result.stdout.strip())
        return True
    
    except FileNotFoundError:
        print(
            "Error: 'fastzipx' command not found.",
            "Please ensure it is installed and in your system's PATH.",
            file=sys.stderr
        )
        # Exit the script immediately if the tool is not found
        sys.exit(1)

    except subprocess.CalledProcessError as e:
        print(f"[!] Error compressing '{file_path}'.", file=sys.stderr)
        print(f"    Return code: {e.returncode}", file=sys.stderr)
        if e.stderr:
            print(f"    Error details: {e.stderr.strip()}", file=sys.stderr)
        return False

def main():
    """
    Parses command-line arguments and orchestrates file compression.
    """
    parser = argparse.ArgumentParser(
        description="A wrapper script to compress files using 'fastzipx' with specific flags."
    )
    parser.add_argument(
        "files",
        metavar="FILE",
        nargs="+",  # Accept one or more file paths
        help="One or more files to compress."
    )
    
    args = parser.parse_args()
    
    successful_compressions = 0
    failed_compressions = 0

    for file_path in args.files:
        if compress_file(file_path):
            successful_compressions += 1
        else:
            failed_compressions += 1
            
    print("\n--- Compression Summary ---")
    print(f"Succeeded: {successful_compressions} file(s)")
    print(f"Failed:    {failed_compressions} file(s)")
    
    if failed_compressions > 0:
        sys.exit(1)

if __name__ == "__main__":
    main()
```