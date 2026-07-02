#!/usr/bin/env python3
"""MongoDB user data simulator for ETL testing.

This script generates realistic fake user data and inserts it into MongoDB.
It simulates a production application continuously producing user records
that the ETL MongoDB extractor will later consume.

Usage:
    python scripts/generate_mongo_users.py --count 100
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from faker import Faker

import pymongo
from dotenv import load_dotenv

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


class MongoUserSimulator:
    """Generates and inserts fake user data into MongoDB."""

    def __init__(self, mongo_uri: str, mongo_db: str, collection: str = "users") -> None:
        """Initialize the simulator with MongoDB connection settings.

        Args:
            mongo_uri: MongoDB connection URI.
            mongo_db: MongoDB database name.
            collection: MongoDB collection name (default: "users").

        Raises:
            ValueError: If connection settings are invalid.
        """
        if not mongo_uri or not mongo_db:
            raise ValueError("mongo_uri and mongo_db must not be empty")

        self.mongo_uri = mongo_uri
        self.mongo_db = mongo_db
        self.collection_name = collection
        self.client: pymongo.MongoClient | None = None
        self.faker = Faker()

    def connect(self) -> None:
        """Establish connection to MongoDB.

        Raises:
            pymongo.errors.ConnectionFailure: If connection fails.
        """
        try:
            self.client = pymongo.MongoClient(self.mongo_uri, serverSelectionTimeoutMS=5000)
            # Verify connection
            self.client.admin.command("ping")
            logger.info("Connected to MongoDB at %s", self.mongo_uri)
        except pymongo.errors.ConnectionFailure as exc:
            logger.error("Failed to connect to MongoDB: %s", exc)
            raise

    def disconnect(self) -> None:
        """Close MongoDB connection."""
        if self.client:
            try:
                self.client.close()
                logger.info("Disconnected from MongoDB")
            except Exception as exc:
                logger.warning("Error disconnecting from MongoDB: %s", exc)

    def _generate_user(self, user_id: int) -> dict[str, Any]:
        """Generate a single fake user document.

        Args:
            user_id: Unique user identifier.

        Returns:
            Dictionary containing user data.
        """
        now = datetime.now(timezone.utc)

        return {
            "user_id": user_id,
            "first_name": self.faker.first_name(),
            "last_name": self.faker.last_name(),
            "email": self.faker.email(),
            "phone": self.faker.phone_number(),
            "gender": self.faker.random_element(["M", "F", "Other"]),
            "date_of_birth": self.faker.date_of_birth(minimum_age=18, maximum_age=80).isoformat(),
            "city": self.faker.city(),
            "country": self.faker.country(),
            "address": self.faker.address(),
            "is_active": self.faker.boolean(chance_of_getting_true=85),
            "signup_source": self.faker.random_element(["web", "mobile_app", "partner", "referral"]),
            "membership": self.faker.random_element(["free", "basic", "premium", "enterprise"]),
            "created_at": now.isoformat(),
            "updated_at": now.isoformat(),
        }

    def generate_and_insert(self, count: int) -> dict[str, Any]:
        """Generate and insert fake users into MongoDB.

        Args:
            count: Number of users to generate and insert.

        Returns:
            Summary dictionary with insertion statistics.

        Raises:
            RuntimeError: If not connected to MongoDB or insertion fails.
        """
        if not self.client:
            raise RuntimeError("Not connected to MongoDB. Call connect() first.")

        db = self.client[self.mongo_db]
        coll = db[self.collection_name]

        logger.info("Generating and inserting %d user(s)...", count)

        # Determine starting user_id by finding max existing
        existing_max = coll.find_one(sort=[("user_id", pymongo.DESCENDING)])
        start_id = (existing_max["user_id"] + 1) if existing_max else 1

        # Generate documents
        documents = []
        for i in range(count):
            doc = self._generate_user(start_id + i)
            documents.append(doc)

        # Insert documents
        try:
            result = coll.insert_many(documents)
            inserted_count = len(result.inserted_ids)
            logger.info("Successfully inserted %d user(s)", inserted_count)

            return {
                "generated": count,
                "inserted": inserted_count,
                "start_id": start_id,
                "end_id": start_id + count - 1,
            }
        except pymongo.errors.PyMongoError as exc:
            logger.error("Failed to insert users: %s", exc)
            raise RuntimeError(f"Insertion failed: {exc}") from exc

    def get_collection_stats(self) -> dict[str, Any]:
        """Get statistics about the users collection.

        Returns:
            Dictionary with collection stats.

        Raises:
            RuntimeError: If not connected to MongoDB.
        """
        if not self.client:
            raise RuntimeError("Not connected to MongoDB. Call connect() first.")

        db = self.client[self.mongo_db]
        coll = db[self.collection_name]

        count = coll.count_documents({})
        return {"total_documents": count}


def load_env_config() -> tuple[str, str]:
    """Load MongoDB configuration from .env file.

    Returns:
        Tuple of (mongo_uri, mongo_db).

    Raises:
        ValueError: If required environment variables are missing.
    """
    # Load .env from project root
    env_path = Path(__file__).parent.parent / ".env"
    if env_path.exists():
        load_dotenv(env_path)
    else:
        logger.warning("No .env file found at %s", env_path)

    import os

    mongo_uri = os.getenv("MONGO_URI")
    mongo_db = os.getenv("MONGO_DB")

    if not mongo_uri or not mongo_db:
        raise ValueError(
            "Missing required environment variables: MONGO_URI and MONGO_DB. "
            "Ensure .env file is properly configured."
        )

    return mongo_uri, mongo_db


def main() -> int:
    """Main entry point for the MongoDB user simulator.

    Returns:
        Exit code (0 for success, 1 for failure).
    """
    parser = argparse.ArgumentParser(
        description="Generate and insert fake user data into MongoDB for ETL testing.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python scripts/generate_mongo_users.py --count 100
  python scripts/generate_mongo_users.py --count 500
        """,
    )

    parser.add_argument(
        "--count",
        type=int,
        default=100,
        help="Number of users to generate and insert (default: 100)",
    )

    args = parser.parse_args()

    if args.count <= 0:
        logger.error("Count must be a positive integer")
        return 1

    try:
        # Load configuration
        mongo_uri, mongo_db = load_env_config()
        logger.info("Using MongoDB database: %s", mongo_db)

        # Initialize simulator
        simulator = MongoUserSimulator(mongo_uri, mongo_db)

        # Connect to MongoDB
        simulator.connect()

        # Record start time
        start_time = time.time()

        # Generate and insert users
        stats = simulator.generate_and_insert(args.count)

        # Get collection stats
        collection_stats = simulator.get_collection_stats()

        # Calculate execution time
        elapsed_time = time.time() - start_time

        # Display summary
        logger.info("=" * 60)
        logger.info("MongoDB User Data Insertion Summary")
        logger.info("=" * 60)
        logger.info("Users generated:  %d", stats["generated"])
        logger.info("Users inserted:   %d", stats["inserted"])
        logger.info("ID range:         %d to %d", stats["start_id"], stats["end_id"])
        logger.info("Total in collection: %d", collection_stats["total_documents"])
        logger.info("Execution time:   %.2f seconds", elapsed_time)
        logger.info("=" * 60)

        # Disconnect
        simulator.disconnect()

        return 0

    except (ValueError, RuntimeError) as exc:
        logger.error("Error: %s", exc)
        return 1
    except Exception as exc:
        logger.exception("Unexpected error: %s", exc)
        return 1


if __name__ == "__main__":
    sys.exit(main())
