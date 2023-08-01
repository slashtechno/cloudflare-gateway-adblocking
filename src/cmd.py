import utils
import argparse
import os
from loguru import logger

def cli():
    # Set up argparse
    argparser = argparse.ArgumentParser()
    # Add arguments
    argparser.add_argument(
        "--account-id",
        "-a",
        help="Cloudflare account ID - environment variable: CLOUDFLARE_ACCOUNT_ID",
        default=os.environ.get("CLOUDFLARE_ACCOUNT_ID")
    )
    argparser.add_argument('--token',
        '-t', 
        help='Cloudflare API token - environment variable: CLOUDFLARE_TOKEN',
        default=os.environ.get("CLOUDFLARE_TOKEN")
    )
    # Parse arguments
    args = argparser.parse_args()
    logger.debug(args)

    # Load variables
    TOKEN = args.token
    ACCOUNT_ID = args.account_id
    
    # Check if variables are set
    if TOKEN is None or ACCOUNT_ID is None:
        logger.error("No environment variables found. Please create a .env file or .envrc file") # noqa E501
        exit()
    




if __name__ == "__main__":
    cli()
