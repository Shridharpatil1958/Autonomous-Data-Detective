"""Database connection handler using SQLAlchemy and PyMySQL."""

import pandas as pd
import numpy as np
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
import logging
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import Config

logger = logging.getLogger(__name__)


class DatabaseConnection:
    """Manages MySQL database connections."""
    
    _instance = None
    _engine = None
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance
    
    def __init__(self):
        if self._engine is None:
            try:
                self._engine = create_engine(
                    Config.DATABASE_URL,
                    pool_size=10,
                    max_overflow=20,
                    pool_recycle=3600,
                    echo=Config.DEBUG
                )
                self.Session = sessionmaker(bind=self._engine)
                logger.info("Database connection established successfully.")
            except Exception as e:
                logger.error(f"Database connection failed: {e}")
                self._engine = None
    
    @property
    def engine(self):
        return self._engine
    
    def is_connected(self):
        """Check if database is connected."""
        try:
            if self._engine:
                with self._engine.connect() as conn:
                    conn.execute(text("SELECT 1"))
                return True
        except Exception:
            pass
        return False
    
    def execute_query(self, query, params=None):
        """Execute a SQL query and return results as DataFrame."""
        try:
            with self._engine.connect() as conn:
                result = pd.read_sql(text(query), conn, params=params)
            return result
        except Exception as e:
            logger.error(f"Query execution failed: {e}")
            return pd.DataFrame()
    
    def execute_raw(self, query, params=None):
        """Execute a raw SQL query (INSERT, UPDATE, DELETE)."""
        try:
            with self._engine.connect() as conn:
                conn.execute(text(query), params or {})
                conn.commit()
            return True
        except Exception as e:
            logger.error(f"Raw query execution failed: {e}")
            return False
    
    def get_customers(self):
        """Fetch all customers."""
        return self.execute_query("SELECT * FROM customers")
    
    def get_products(self):
        """Fetch all products."""
        return self.execute_query("SELECT * FROM products")
    
    def get_sales(self):
        """Fetch all sales with joins."""
        query = """
            SELECT s.*, c.customer_name, c.city, c.gender, c.age,
                   p.product_name, p.category, p.price
            FROM sales s
            JOIN customers c ON s.customer_id = c.customer_id
            JOIN products p ON s.product_id = p.product_id
            ORDER BY s.sale_date DESC
        """
        return self.execute_query(query)
    
    def get_reviews(self):
        """Fetch all reviews with joins."""
        query = """
            SELECT r.*, c.customer_name, p.product_name, p.category
            FROM reviews r
            JOIN customers c ON r.customer_id = c.customer_id
            JOIN products p ON r.product_id = p.product_id
            ORDER BY r.review_date DESC
        """
        return self.execute_query(query)
    
    def get_transactions(self):
        """Fetch all transactions."""
        query = """
            SELECT t.*, c.customer_name, c.city
            FROM transactions t
            JOIN customers c ON t.customer_id = c.customer_id
            ORDER BY t.transaction_date DESC
        """
        return self.execute_query(query)
    
    def close(self):
        """Close database connection."""
        if self._engine:
            self._engine.dispose()
            logger.info("Database connection closed.")


def get_db():
    """Get database connection instance."""
    return DatabaseConnection()


def load_data_fallback():
    """Load sample data from synthetic generation if database is unavailable."""
    np.random.seed(42)
    n_customers = 200
    n_products = 50
    n_sales = 1000
    
    data = {}
    
    # Cities and categories
    cities = ['New York', 'Los Angeles', 'Chicago', 'Houston', 'Phoenix',
              'Philadelphia', 'San Antonio', 'San Diego', 'Dallas', 'San Jose',
              'Austin', 'Jacksonville', 'Fort Worth', 'Columbus', 'Charlotte']
    genders = ['Male', 'Female']
    contracts = ['Monthly', 'Yearly', 'Two-Year']
    payments = ['Credit Card', 'Bank Transfer', 'Digital Wallet', 'Cash']
    categories = ['Electronics', 'Sports', 'Home', 'Books', 'Beauty']
    regions = ['North', 'South', 'East', 'West', 'Central']
    
    # Generate Customers
    names_male = ['John', 'Michael', 'David', 'James', 'Robert', 'William', 'Richard', 'Joseph', 'Thomas', 'Christopher']
    names_female = ['Sarah', 'Emily', 'Jessica', 'Amanda', 'Ashley', 'Stephanie', 'Nicole', 'Rachel', 'Megan', 'Lauren']
    last_names = ['Smith', 'Johnson', 'Brown', 'Davis', 'Wilson', 'Martinez', 'Anderson', 'Thomas', 'Taylor', 'Garcia',
                  'Lee', 'Harris', 'Clark', 'Lewis', 'Robinson', 'Walker', 'Hall', 'Allen', 'Young', 'King']
    
    customer_names = []
    customer_genders = []
    for i in range(n_customers):
        gender = np.random.choice(genders)
        customer_genders.append(gender)
        if gender == 'Male':
            first = np.random.choice(names_male)
        else:
            first = np.random.choice(names_female)
        last = np.random.choice(last_names)
        customer_names.append(f"{first} {last}")
    
    tenure = np.random.randint(1, 60, n_customers)
    monthly_charges = np.random.uniform(25, 160, n_customers).round(2)
    total_spending = (monthly_charges * tenure * np.random.uniform(0.8, 1.2, n_customers)).round(2)
    support_tickets = np.random.randint(0, 10, n_customers)
    
    # Churn logic: higher tickets + lower tenure + monthly contract = more likely to churn
    churn_prob = (support_tickets / 10 * 0.4 + (1 - tenure / 60) * 0.3 + 
                  np.array([0.3 if c == 'Monthly' else 0.1 for c in np.random.choice(contracts, n_customers)]))
    is_churned = (np.random.random(n_customers) < churn_prob * 0.4).astype(int)
    
    contract_types = np.random.choice(contracts, n_customers)
    
    data['customers'] = pd.DataFrame({
        'customer_id': range(1, n_customers + 1),
        'customer_name': customer_names,
        'gender': customer_genders,
        'age': np.random.randint(18, 65, n_customers),
        'city': np.random.choice(cities, n_customers),
        'join_date': pd.date_range('2019-01-01', periods=n_customers, freq='3D'),
        'total_spending': total_spending,
        'is_churned': is_churned,
        'monthly_charges': monthly_charges,
        'tenure_months': tenure,
        'support_tickets': support_tickets,
        'contract_type': contract_types,
        'payment_method': np.random.choice(payments, n_customers)
    })
    
    # Generate Products
    product_names = {
        'Electronics': ['Laptop Pro', 'Wireless Mouse', 'USB-C Hub', 'Mechanical Keyboard', '4K Monitor',
                       'Headphones', 'Webcam HD', 'External SSD', 'Tablet', 'Smart Watch'],
        'Sports': ['Running Shoes', 'Yoga Mat', 'Dumbbell Set', 'Tennis Racket', 'Basketball',
                  'Cycling Helmet', 'Resistance Bands', 'Swimming Goggles', 'Fitness Tracker', 'Jump Rope'],
        'Home': ['Coffee Maker', 'Blender', 'Air Purifier', 'Robot Vacuum', 'Smart Thermostat',
                'LED Desk Lamp', 'Electric Kettle', 'Toaster Oven', 'Humidifier', 'Smart Speaker'],
        'Books': ['Python Programming', 'Data Science Handbook', 'ML Guide', 'Business Strategy', 'Marketing Analytics',
                 'Financial Planning', 'Leadership Skills', 'AI Revolution', 'Deep Learning', 'Statistics'],
        'Beauty': ['Face Cream', 'Shampoo Premium', 'Perfume Collection', 'Sunscreen SPF50', 'Hair Dryer Pro',
                  'Vitamin C Serum', 'Body Lotion', 'Makeup Kit', 'Essential Oils', 'Nail Polish Set']
    }
    
    all_products = []
    pid = 1
    for cat, names in product_names.items():
        for name in names:
            price = np.random.uniform(15, 1300) if cat == 'Electronics' else np.random.uniform(10, 300)
            all_products.append({
                'product_id': pid,
                'product_name': name,
                'category': cat,
                'price': round(price, 2),
                'stock_quantity': np.random.randint(50, 800),
                'rating': round(np.random.uniform(3.5, 5.0), 2)
            })
            pid += 1
    
    data['products'] = pd.DataFrame(all_products)
    
    # Generate Sales
    sale_dates = pd.date_range('2022-01-01', periods=730, freq='D')
    sale_customer_ids = np.random.randint(1, n_customers + 1, n_sales)
    sale_product_ids = np.random.randint(1, n_products + 1, n_sales)
    quantities = np.random.randint(1, 6, n_sales)
    discounts = np.random.uniform(0, 25, n_sales).round(2)
    
    prices = data['products'].set_index('product_id')['price']
    revenues = []
    for i in range(n_sales):
        pid = sale_product_ids[i]
        price = prices.get(pid, 50.0)
        rev = price * quantities[i] * (1 - discounts[i] / 100)
        revenues.append(round(rev, 2))
    
    data['sales'] = pd.DataFrame({
        'sale_id': range(1, n_sales + 1),
        'customer_id': sale_customer_ids,
        'product_id': sale_product_ids,
        'quantity': quantities,
        'sale_date': np.random.choice(sale_dates, n_sales),
        'revenue': revenues,
        'discount_percent': discounts,
        'region': np.random.choice(regions, n_sales)
    })
    
    # Add product info to sales
    data['sales'] = data['sales'].merge(
        data['products'][['product_id', 'product_name', 'category', 'price']],
        on='product_id', how='left'
    )
    data['sales'] = data['sales'].merge(
        data['customers'][['customer_id', 'customer_name', 'city', 'gender', 'age']],
        on='customer_id', how='left'
    )
    
    # Generate Reviews
    positive_reviews = [
        "Excellent product! Highly recommended for everyone.",
        "Great quality and fast delivery. Very satisfied.",
        "Love it! Best purchase this year. Amazing value.",
        "Amazing value for money. Would buy again.",
        "Perfect! Exceeded my expectations completely.",
        "Outstanding quality and beautiful design.",
        "Very satisfied with this purchase. Top notch!",
        "Wonderful product, will definitely buy again.",
        "Fantastic experience from start to finish.",
        "Superb quality. Exactly what I was looking for."
    ]
    negative_reviews = [
        "Terrible quality. Complete waste of money.",
        "Product broke within a week. Very disappointed.",
        "Very disappointed with this purchase. Awful.",
        "Would not recommend to anyone. Poor quality.",
        "Poor quality and terrible customer service.",
        "Not worth the price at all. Regret buying.",
        "Horrible experience. Want a full refund.",
        "Product does not match the description at all.",
        "Cheaply made and fell apart quickly.",
        "Worst purchase ever. Stay away from this."
    ]
    neutral_reviews = [
        "Decent product for the price. Nothing special.",
        "It is okay, meets basic needs adequately.",
        "Average quality, does what it is supposed to.",
        "Could be better but acceptable for the cost.",
        "Neither great nor terrible. Just average."
    ]
    
    n_reviews = 100
    review_texts = []
    ratings = []
    for _ in range(n_reviews):
        r = np.random.random()
        if r < 0.5:
            review_texts.append(np.random.choice(positive_reviews))
            ratings.append(np.random.choice([4, 5]))
        elif r < 0.75:
            review_texts.append(np.random.choice(neutral_reviews))
            ratings.append(3)
        else:
            review_texts.append(np.random.choice(negative_reviews))
            ratings.append(np.random.choice([1, 2]))
    
    data['reviews'] = pd.DataFrame({
        'review_id': range(1, n_reviews + 1),
        'customer_id': np.random.randint(1, n_customers + 1, n_reviews),
        'product_id': np.random.randint(1, n_products + 1, n_reviews),
        'review_text': review_texts,
        'rating': ratings,
        'review_date': pd.date_range('2023-01-01', periods=n_reviews, freq='2D')
    })
    
    # Add product/customer info to reviews
    data['reviews'] = data['reviews'].merge(
        data['products'][['product_id', 'product_name', 'category']],
        on='product_id', how='left'
    )
    data['reviews'] = data['reviews'].merge(
        data['customers'][['customer_id', 'customer_name']],
        on='customer_id', how='left'
    )
    
    # Generate Transactions (for anomaly detection)
    n_transactions = 500
    amounts = []
    is_fraud = []
    for _ in range(n_transactions):
        if np.random.random() < 0.05:
            amounts.append(round(np.random.uniform(2000, 10000), 2))
            is_fraud.append(1)
        else:
            amounts.append(round(np.random.uniform(10, 500), 2))
            is_fraud.append(0)
    
    merchant_categories = ['Electronics Store', 'Grocery', 'Restaurant', 'Gas Station',
                          'Online Shopping', 'Travel', 'Entertainment', 'Healthcare']
    locations = ['New York', 'Los Angeles', 'Chicago', 'Houston', 'Phoenix', 'International']
    
    data['transactions'] = pd.DataFrame({
        'transaction_id': range(1, n_transactions + 1),
        'customer_id': np.random.randint(1, n_customers + 1, n_transactions),
        'amount': amounts,
        'transaction_date': pd.date_range('2023-01-01', periods=n_transactions, freq='12H'),
        'transaction_type': np.random.choice(['Purchase', 'Refund', 'Transfer'], n_transactions),
        'is_fraud': is_fraud,
        'merchant_category': np.random.choice(merchant_categories, n_transactions),
        'location': np.random.choice(locations, n_transactions)
    })
    
    # Add customer info to transactions
    data['transactions'] = data['transactions'].merge(
        data['customers'][['customer_id', 'customer_name', 'city']],
        on='customer_id', how='left'
    )
    
    return data