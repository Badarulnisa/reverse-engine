import string
import logging

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("FanoutEngine")

class FanoutEngine:
    def __init__(self, api_client, limit=100, max_depth=6):
        self.api_client = api_client
        self.limit = limit
        self.max_depth = max_depth
        self.charset = string.ascii_lowercase + string.digits

    def recursive_prefix_search(self, prefix="", collected_records=None):
        if collected_records is None:
            collected_records = []

        if len(prefix) > self.max_depth:
            logger.warning(f"Max depth reached for '{prefix}'. Skipping further branches.")
            return collected_records
        
        try:
            results = self.api_client.execute_search(customer_name=prefix)
        except Exception as e:
            logger.error(f"Request failed for prefix '{prefix}': {e}")
            return collected_records

        count = len(results)

        if count < self.limit:
            if count > 0:
                logger.info(f"'{prefix}' complete: {count} records.")
                collected_records.extend(results)
            return collected_records

        logger.warning(f"'{prefix}' hit cap ({count} records). Branching deeper...")
        
        for char in self.charset:
            next_prefix = prefix + char
            self.recursive_prefix_search(prefix=next_prefix, collected_records=collected_records)

        return collected_records

    def run(self):
        logger.info("Starting recursive fan-out traversal...")
        raw_records = self.recursive_prefix_search(prefix="")
        logger.info(f"Fan-out complete. Total raw records: {len(raw_records)}")
        return raw_records