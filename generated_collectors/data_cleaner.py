import re
import pandas as pd

class DataProcessor:
    @staticmethod
    def clean_record_fields(record):
        """Strips HTML tags like <br> from string values."""
        cleaned_record = {}
        for key, value in record.items():
            if isinstance(value, str):
                # Replace HTML breaks with a single line space
                cleaned = re.sub(r'<br\s*/?>', ' ', value, flags=re.IGNORECASE)
                cleaned_record[key] = cleaned.strip()
            else:
                cleaned_record[key] = value
        return cleaned_record

    @staticmethod
    def deduplicate(records, primary_key="licNo"):
        """Deduplicates a list of dictionaries based on a unique identifier."""
        seen_keys = set()
        unique_records = []
        
        for raw_record in records:
            record = DataProcessor.clean_record_fields(raw_record)
            key_val = record.get(primary_key)
            
            if key_val and key_val not in seen_keys:
                seen_keys.add(key_val)
                unique_records.append(record)
            elif not key_val:
                # Retain entries missing the primary key to avoid data loss
                unique_records.append(record)
                
        return unique_records

    @staticmethod
    def export_to_excel(records, output_filepath):
        df = pd.DataFrame(records)
        df.to_excel(output_filepath, index=False)