import sqlite3
import pandas as pd
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent
ARCHIVE_DIR = PROJECT_ROOT / "archive"
DB_PATH = PROJECT_ROOT / "olist_base.db"


def build_base_db():
    if DB_PATH.exists():
        DB_PATH.unlink()
    
    conn = sqlite3.connect(DB_PATH)
    
    print("1/4 Translating category names...")
    df_trans = pd.read_csv(ARCHIVE_DIR / "product_category_name_translation.csv")
    df_products = pd.read_csv(ARCHIVE_DIR / "olist_products_dataset.csv")
    df_products = df_products.merge(df_trans, on='product_category_name', how='left')
    df_products['product_category_name'] = df_products['product_category_name_english'].fillna(df_products['product_category_name'])
    df_products.drop(columns=['product_category_name_english'], inplace=True)
    
    print("2/4 Computing baseline synthetic unit_cost & discount columns...")
    df_items = pd.read_csv(ARCHIVE_DIR / "olist_order_items_dataset.csv")
    avg_prices = df_items.groupby('product_id')['price'].mean().reset_index()
    avg_prices['unit_cost'] = (avg_prices['price'] * 0.60).round(2)
    
    df_products = df_products.merge(avg_prices[['product_id', 'unit_cost']], on='product_id', how='left')
    df_products['unit_cost'] = df_products['unit_cost'].fillna(25.00)
    
    df_items['discount_code'] = None
    df_items['discount_amount'] = 0.00
    
    print("3/4 Ingesting tables into SQLite...")
    df_orders = pd.read_csv(ARCHIVE_DIR / "olist_orders_dataset.csv")
    df_payments = pd.read_csv(ARCHIVE_DIR / "olist_order_payments_dataset.csv")
    df_reviews = pd.read_csv(ARCHIVE_DIR / "olist_order_reviews_dataset.csv")
    df_sellers = pd.read_csv(ARCHIVE_DIR / "olist_sellers_dataset.csv")
    df_customers = pd.read_csv(ARCHIVE_DIR / "olist_customers_dataset.csv")
    df_geo = pd.read_csv(ARCHIVE_DIR / "olist_geolocation_dataset.csv")
    
    df_orders.to_sql('orders', conn, index=False, if_exists='replace')
    df_items.to_sql('order_items', conn, index=False, if_exists='replace')
    df_payments.to_sql('order_payments', conn, index=False, if_exists='replace')
    df_reviews.to_sql('order_reviews', conn, index=False, if_exists='replace')
    df_products.to_sql('products', conn, index=False, if_exists='replace')
    df_sellers.to_sql('sellers', conn, index=False, if_exists='replace')
    df_customers.to_sql('customers', conn, index=False, if_exists='replace')
    df_geo.to_sql('geolocation', conn, index=False, if_exists='replace')
    
    print("4/4 Creating foreign key indexes...")
    cursor = conn.cursor()
    cursor.execute("CREATE INDEX idx_orders_timestamp ON orders(order_purchase_timestamp);")
    cursor.execute("CREATE INDEX idx_items_order_id ON order_items(order_id);")
    cursor.execute("CREATE INDEX idx_items_product_id ON order_items(product_id);")
    cursor.execute("CREATE INDEX idx_items_seller_id ON order_items(seller_id);")
    conn.commit()
    conn.close()
    print(f"Successfully generated baseline database: {DB_PATH}")

if __name__ == '__main__':
    build_base_db()