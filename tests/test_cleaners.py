import pytest
from scraper.cleaners import (
    clean_html_description,
    normalize_price,
    calculate_discount_percentage,
    normalize_url,
    process_images,
    extract_material_and_pieces,
    deduplicate_products
)

def test_clean_html_description():
    html = "<p>Product Description: <b>Exquisite suit</b>.<br>Fabric: Lawn</p><ul><li>3 Piece</li></ul>"
    cleaned = clean_html_description(html)
    assert "Exquisite suit" in cleaned
    assert "Fabric: Lawn" in cleaned
    assert "3 Piece" in cleaned
    assert "<p>" not in cleaned

def test_normalize_price():
    assert normalize_price("Rs. 5,999") == 5999
    assert normalize_price("PKR 12,500.00") == 12500
    assert normalize_price(4500) == 4500
    assert normalize_price(None) is None
    assert normalize_price("Invalid") is None

def test_calculate_discount_percentage():
    assert calculate_discount_percentage(10000, 7500) == "25%"
    assert calculate_discount_percentage(5000, 5000) == ""
    assert calculate_discount_percentage(None, 4000) == ""

def test_normalize_url():
    assert normalize_url("/products/test") == "https://tehzeeblibas.com/products/test"
    assert normalize_url("//cdn.shopify.com/image.jpg") == "https://cdn.shopify.com/image.jpg"
    assert normalize_url("https://tehzeeblibas.com/test") == "https://tehzeeblibas.com/test"

def test_process_images():
    images = [
        {"src": "https://cdn.shopify.com/img1.jpg?v=123"},
        {"src": "https://cdn.shopify.com/img1.jpg?v=123"},  # Duplicate
        {"src": "https://cdn.shopify.com/img2.jpg"}
    ]
    main_img, additional_imgs = process_images(images)
    assert "img1.jpg" in main_img
    assert "img2.jpg" in additional_imgs
    assert "|" not in main_img

def test_extract_material_and_pieces():
    text = "Beautiful 3-piece suit made from premium Lawn and Chiffon dupatta"
    tags = ["Lawn", "Chiffon"]
    mat, pieces = extract_material_and_pieces(text, tags)
    assert "Lawn" in mat
    assert "Chiffon" in mat
    assert pieces == "3 Piece"

def test_deduplicate_products():
    prods = [
        {"product_id": "101", "product_name": "A"},
        {"product_id": "101", "product_name": "A Duplicate"},
        {"product_id": "102", "product_name": "B"}
    ]
    unique, dupes = deduplicate_products(prods)
    assert len(unique) == 2
    assert dupes == 1
