# To run from the root project directory, run the following command:
# python -m src.__main__
# python -m src # also works because __main__ is the default module
import argparse
import os
from pathlib import Path
from sys import exit, stderr

import dotenv
from loguru import logger

from .utils import delete, upload, utils

TOKEN = None
ACCOUNT_ID = None


def main():
    # Setup logging
    logger.remove()
    # ^10 is a formatting directive to center with a padding of 10
    logger_format = "<green>{time:YYYY-MM-DD HH:mm:ss}</green> |<level>{level: ^10}</level>| <level>{message}</level>"  # noqa E501
    logger.add(stderr, format=logger_format, colorize=True, level="DEBUG")

    # Load .env if it exists
    # This must precede the argparse setup as env variables are used as default values
    if Path(".env").is_file():
        dotenv.load_dotenv()
        logger.info("Loaded .env file")
    else:
        logger.info("No .env file found")

    # Parse arguments

    # Set up argparse
    argparser = argparse.ArgumentParser(
        prog="Cloudflare Gateway Adblocking",
        description="Serverless adblocking via Cloudflare Zero Trust Gateway",
        epilog=":)",
    )

    # Add argument groups
    credential_args = argparser.add_argument_group("Cloudflare Credentials")

    # Add arguments
    credential_args.add_argument(
        "--account-id",
        "-a",
        help="Cloudflare account ID - environment variable: CLOUDFLARE_ACCOUNT_ID",
        default=os.environ.get("CLOUDFLARE_ACCOUNT_ID"),
    )
    credential_args.add_argument(
        "--token",
        "-t",
        help="Cloudflare API token - environment variable: CLOUDFLARE_TOKEN",
        default=os.environ.get("CLOUDFLARE_TOKEN"),
    )

    # Add subcommands
    subparsers = argparser.add_subparsers(
        title="subcommands",
        description="",
        help="Subcommands to preform operations",
        dest="subcommand",
    )
    # Add subcommand: upload
    upload_parser = subparsers.add_parser(
        "upload", help="Upload adblock lists to Cloudflare"
    )
    upload_parser.set_defaults(func=upload_to_cloudflare)
    upload_parser.add_argument(
        "--blocklists",
        "-b",
        help="Either a blocklist hosts file or a directory containing blocklist hosts files",  # noqa E501
        default="blocklists", # Not really needed because the get_blocklists function will default to this  # noqa: E501
    )
    upload_parser.add_argument(
        "--whitelists",
        "-w",
        help="Either a whitelist hosts file or a directory containing whitelist hosts files",  # noqa E501
        default="whitelist.txt",  # Not really needed because the apply_whitelists function will default to this  # noqa: E501
    )
    # Add subcommand: delete
    delete_parser = subparsers.add_parser(
        "delete", help="Delete adblock lists from Cloudflare"
    )
    delete_parser.set_defaults(func=delete_from_cloudflare)

    args = argparser.parse_args()

    logger.debug(args)

    # Load variables
    global TOKEN
    global ACCOUNT_ID
    TOKEN = args.token
    ACCOUNT_ID = args.account_id
    # Check if variables are set
    if TOKEN is None or ACCOUNT_ID is None:
        logger.error(
            "No environment variables found. Please create a .env file or .envrc file"
        )  # noqa E501
        exit(1)
    try:
        args.func(args)
    except AttributeError as e:
        logger.error("No subcommand specified")
        argparser.print_help()
        exit(1)


def upload_to_cloudflare(args):
    logger.info("Uploading to Cloudflare")
    blocklists = upload.get_blocklists(args.blocklists)
    blocklists = upload.apply_whitelists(blocklists, args.whitelists)
    lists = upload.split_list(blocklists)
    upload.upload_to_cloudflare(lists, ACCOUNT_ID, TOKEN)
    cloud_lists = utils.get_lists(ACCOUNT_ID, TOKEN)
    cloud_lists = utils.filter_adblock_lists(cloud_lists)
    upload.create_dns_policy(cloud_lists, ACCOUNT_ID, TOKEN)


def delete_from_cloudflare(args):
    logger.info("Deleting from Cloudflare")
    rules = utils.get_gateway_rules(ACCOUNT_ID, TOKEN)
    delete.delete_adblock_policy(rules, ACCOUNT_ID, TOKEN)
    lists = utils.get_lists(ACCOUNT_ID, TOKEN)
    lists = utils.filter_adblock_lists(lists)
    delete.delete_adblock_list(lists, ACCOUNT_ID, TOKEN)


if __name__ == "__main__":
    main()
