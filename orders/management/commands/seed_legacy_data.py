"""
Management command to seed the database with 500,000 legacy order records.
Uses bulk_create for efficiency.
"""

import random
import decimal
from django.core.management.base import BaseCommand
from orders.models import LegacyOrder


# Sample data pools for realistic generation
CUSTOMER_DOMAINS = [
    'gmail.com', 'yahoo.com', 'hotmail.com', 'outlook.com',
    'example.com', 'company.org', 'business.net', 'mail.com',
]

FIRST_NAMES = [
    'alice', 'bob', 'charlie', 'diana', 'edward', 'fiona', 'george', 'helen',
    'ivan', 'julia', 'kevin', 'laura', 'michael', 'nancy', 'oscar', 'patricia',
    'quentin', 'rachel', 'samuel', 'tina', 'ulrich', 'vera', 'william', 'xena',
    'yusuf', 'zara',
]

SKU_PREFIXES = ['SKU-A', 'SKU-B', 'SKU-C', 'SKU-D', 'SKU-E', 'SKU-F', 'SKU-G', 'SKU-H']
SKU_NUMBERS = [str(i) for i in range(1, 100)]

TOTAL_RECORDS = 500_000
DEFAULT_BATCH_SIZE = 5000


class Command(BaseCommand):
    help = 'Seeds the database with 500,000 legacy order records for ETL testing.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--batch-size',
            type=int,
            default=DEFAULT_BATCH_SIZE,
            help=f'Number of records to insert per batch (default: {DEFAULT_BATCH_SIZE})',
        )
        parser.add_argument(
            '--count',
            type=int,
            default=TOTAL_RECORDS,
            help=f'Total number of records to create (default: {TOTAL_RECORDS})',
        )
        parser.add_argument(
            '--clear',
            action='store_true',
            help='Clear existing legacy orders before seeding.',
        )

    def handle(self, *args, **options):
        batch_size = options['batch_size']
        total_count = options['count']

        if options['clear']:
            self.stdout.write('Clearing existing LegacyOrder records...')
            deleted, _ = LegacyOrder.objects.all().delete()
            self.stdout.write(self.style.WARNING(f'Deleted {deleted} existing records.'))

        existing_count = LegacyOrder.objects.count()
        if existing_count > 0:
            self.stdout.write(
                self.style.WARNING(
                    f'Database already has {existing_count} LegacyOrder records. '
                    f'Use --clear to reset before reseeding.'
                )
            )
            return

        self.stdout.write(
            f'Seeding {total_count:,} legacy order records '
            f'(batch size: {batch_size:,})...'
        )

        records_created = 0
        batch = []

        for i in range(1, total_count + 1):
            external_id = f'legacy-{i}'
            raw_data = self._generate_raw_data(i)

            batch.append(
                LegacyOrder(
                    external_id=external_id,
                    raw_data=raw_data,
                    migrated=False,
                )
            )

            if len(batch) >= batch_size:
                LegacyOrder.objects.bulk_create(batch, ignore_conflicts=True)
                records_created += len(batch)
                batch = []
                self.stdout.write(
                    f'  Inserted {records_created:,} / {total_count:,} records...'
                )

        # Insert remaining records
        if batch:
            LegacyOrder.objects.bulk_create(batch, ignore_conflicts=True)
            records_created += len(batch)

        self.stdout.write(
            self.style.SUCCESS(
                f'\nSuccessfully seeded {records_created:,} legacy order records.'
            )
        )

    def _generate_raw_data(self, index: int) -> dict:
        """Generate realistic raw order data for a given index."""
        # Deterministic but varied data based on index
        rng = random.Random(index)

        first_name = rng.choice(FIRST_NAMES)
        domain = rng.choice(CUSTOMER_DOMAINS)
        suffix = index % 1000
        customer_email = f'{first_name}{suffix}@{domain}'

        # Generate 1-4 line items
        num_items = rng.randint(1, 4)
        items = []
        total = decimal.Decimal('0.00')

        for _ in range(num_items):
            sku = rng.choice(SKU_PREFIXES) + rng.choice(SKU_NUMBERS)
            quantity = rng.randint(1, 5)
            # Unit price between $5.00 and $299.99
            unit_price = decimal.Decimal(str(round(rng.uniform(5.0, 299.99), 2)))
            line_total = unit_price * quantity
            total += line_total

            items.append({
                'sku': sku,
                'quantity': quantity,
                'unit_price': str(unit_price),
            })

        return {
            'customer_email': customer_email,
            'total': str(total.quantize(decimal.Decimal('0.01'))),
            'items': items,
        }
