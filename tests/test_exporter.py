import pytest
from pathlib import Path
import csv
import json
from scraper.exporter import export_to_csv, load_progress, save_progress

def test_export_to_csv(tmp_path):
    output_file = tmp_path / "test_products.csv"
    sample_data = [
        {
            "product_id": "1001",
            "sku": "PAK-1001",
            "product_name": 'Suit with "Quotes" & Commas, Special',
            "description": "Clean description text",
            "brand": "Tehzeeb Libas",
            "category": "Everyday",
            "price": 4500,
            "currency": "PKR",
            "availability": "In Stock"
        }
    ]

    success = export_to_csv(sample_data, output_file)
    assert success is True
    assert output_file.exists()

    with open(output_file, "r", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
        assert len(rows) == 1
        assert rows[0]["product_id"] == "1001"
        assert rows[0]["product_name"] == 'Suit with "Quotes" & Commas, Special'

def test_progress_save_and_load(tmp_path, monkeypatch):
    progress_file = tmp_path / "progress.json"
    monkeypatch.setattr("config.PROGRESS_JSON_PATH", progress_file)

    scraped_ids = ["1001", "1002"]
    scraped_products = [{"product_id": "1001"}, {"product_id": "1002"}]

    save_progress(scraped_ids, scraped_products, 0)
    loaded = load_progress()

    assert loaded["scraped_ids"] == ["1001", "1002"]
    assert len(loaded["scraped_products"]) == 2
