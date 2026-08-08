import csv
import os
import threading
from app.collectors.parser import RegistryRecord

HEADERS = [
    "Business Name", "License Number", "Issue Date", "Expiry Date",
    "Address", "Manager", "Activity", "Status",
]

class StreamingCSVWriter:
    def __init__(self, path: str, dedupe: bool = True, resume: bool = True):
        self.path = path
        self.dedupe = dedupe
        self._lock = threading.Lock()
        self._seen: set[str] = set()

        file_exists = os.path.exists(path)

        if resume and file_exists:
            self._load_seen_from_existing()
            self._fh = open(path, "a", newline="", encoding="utf-8")
            self._writer = csv.writer(self._fh)
        else:
            self._fh = open(path, "w", newline="", encoding="utf-8")
            self._writer = csv.writer(self._fh)
            self._writer.writerow(HEADERS)
            self._fh.flush()

    def _load_seen_from_existing(self) -> None:
        with open(self.path, "r", newline="", encoding="utf-8") as f:
            reader = csv.reader(f)
            next(reader, None)
            for row in reader:
                if len(row) > 1 and row[1]:
                    self._seen.add(row[1].strip())

    def write(self, record: RegistryRecord) -> bool:
        key = (record.license_number or "").strip()
        with self._lock:
            if self.dedupe and key and key in self._seen:
                return False
            self._writer.writerow([
                record.business_name,
                record.license_number,
                record.issue_date,
                record.expiry_date,
                record.address,
                record.manager,
                record.activity,
                record.status,
            ])
            self._fh.flush()
            if self.dedupe and key:
                self._seen.add(key)
            return True

    def write_many(self, records: list[RegistryRecord]) -> int:
        return sum(self.write(r) for r in records)

    @property
    def seen_count(self) -> int:
        return len(self._seen)

    def close(self) -> None:
        with self._lock:
            if not self._fh.closed:
                self._fh.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()

