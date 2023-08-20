import requests
import sys
from loguru import logger
from configparser import ConfigParser

logger.add(sys.stdout, level="INFO")

class Downloader:
    def __init__(self, url_file, outfile):
        self.urls = self.read_urls_from_ini(url_file)
        self.outfile = outfile

    def read_urls_from_ini(self, url_file):
        config = ConfigParser()
        config.read(url_file)

        urls = []
        for section in config.sections():
            for key in config.options(section):
                urls.append(config.get(section, key))
        
        return urls

    def download_hosts(self):
        for url in self.urls:
            response = requests.get(url)
            if response.status_code == 200:
                logger.info(f"Successfully downloaded URL: {url}")
                with open(self.outfile, "a") as file:
                    file.write(response.text)
            else:
                logger.error(f"Failed to download URL: {url}, Response code: {response.status_code}")
                
