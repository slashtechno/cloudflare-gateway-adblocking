# To run from the root project directory, run the following command:
# python -m src.__main__
# python -m src # also works because __main__ is the default module
import argparse
import asyncio
import os
from pathlib import Path
from sys import exit, stderr

import dotenv
from loguru import logger

from .utils import delete, upload, utils

TOKEN = None
ACCOUNT_ID = None


def main():
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

    # General arguments
    argparser.add_argument(
        "--log-level", "-l", help="Log level", default="INFO"
    )  # noqa E501

    # Credential arguments
    credential_args.add_argument(
        "--account-id",
        "-a",
        help="Cloudflare account ID - environment variable: CLOUDFLARE_ACCOUNT_ID",
    )
    credential_args.add_argument(
        "--token",
        "-t",
        help="Cloudflare API token - environment variable: CLOUDFLARE_TOKEN",
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
        default="blocklists",  # Not really needed because the get_blocklists function will default to this  # noqa: E501
    )
    upload_parser.add_argument(
        "--whitelists",
        "-w",
        help="Either a whitelist hosts file or a directory containing whitelist hosts files",  # noqa E501
        default="whitelists",  # Not really needed because the apply_whitelists function will default to this  # noqa: E501
    )
    # Add subcommand: delete
    delete_parser = subparsers.add_parser(
        "delete", help="Delete adblock lists from Cloudflare"
    )
    delete_parser.set_defaults(func=delete_from_cloudflare)

    args = argparser.parse_args()

    # Set up logging
    set_primary_logger(args.log_level)
    logger.debug(args)

    # Load variables
    global TOKEN
    global ACCOUNT_ID
    TOKEN = args.token
    ACCOUNT_ID = args.account_id

    # Check if credentials are set, if they are not, attempt to load from environment variables and .env  # noqa E501
    if TOKEN is None or ACCOUNT_ID is None:
        logger.info("No credentials provided with flags")
        if Path(".env").is_file():
            logger.debug("Loading .env")
            dotenv.load_dotenv(Path(Path.cwd() / ".env"))
        else:
            logger.debug("No .env file found")
        try:
            TOKEN = os.environ["CLOUDFLARE_TOKEN"]
            ACCOUNT_ID = os.environ["CLOUDFLARE_ACCOUNT_ID"]
            logger.info("Loaded credentials from environment variables")
        except KeyError:
            logger.error("No credentials provided")
            argparser.print_help()
            exit(1)
    # For debugging, print cwd
    logger.debug(f"Current working directory: {Path.cwd()}")
    try:
        args.func(args)
    except AttributeError:
        logger.error("No subcommand specified")
        argparser.print_help()
        exit(1)


def upload_to_cloudflare(args):
    logger.info("Uploading to Cloudflare")
    blocklists = upload.get_blocklists(args.blocklists)
    blocklists = upload.apply_whitelists(blocklists, args.whitelists)
    lists = upload.split_list(blocklists)
    asyncio.run(upload.upload_to_cloudflare(lists, ACCOUNT_ID, TOKEN))
    cloud_lists = utils.get_lists(ACCOUNT_ID, TOKEN)
    cloud_lists = utils.filter_adblock_lists(cloud_lists)
    upload.create_dns_policy(cloud_lists, ACCOUNT_ID, TOKEN)


def delete_from_cloudflare(args):
    logger.info("Deleting from Cloudflare")
    rules = utils.get_gateway_rules(ACCOUNT_ID, TOKEN)
    delete.delete_adblock_policy(rules, ACCOUNT_ID, TOKEN)
    lists = utils.get_lists(ACCOUNT_ID, TOKEN)
    lists = utils.filter_adblock_lists(lists)
    asyncio.run(delete.delete_adblock_list(lists, ACCOUNT_ID, TOKEN))


def set_primary_logger(log_level):
    logger.remove()
    # ^10 is a formatting directive to center with a padding of 10
    logger_format = "<green>{time:YYYY-MM-DD HH:mm:ss}</green> |<level>{level: ^10}</level>| <level>{message}</level>"  # noqa E501
    logger.add(stderr, format=logger_format, colorize=True, level=log_level)


if __name__ == "__main__":
    main()
