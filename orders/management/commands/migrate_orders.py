"""
Production-grade ETL management command.

Migrates legacy order records into normalized Order and OrderLine tables.
Features:
- Memory-efficient processing via iterator()
- Batch inserts via bulk_create()
- Idempotent (safe to re-run)
- Resumable via --start-from
- Dry-run support
- Progress reporting and throughput metrics
- Atomic transactions per batch for data integrity
"""

import time
import tracemalloc
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from orders.models import LegacyOrder, Order, OrderLine


DEFAULT_BATCH_SIZE = 1000


class Command(BaseCommand):
    help = (
        'Migrates legacy order records into normalized Order and OrderLine tables. '
        'Supports batched, memory-efficient, idempotent, and resumable processing.'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--batch-size',
            type=int,
            default=DEFAULT_BATCH_SIZE,
            metavar='INTEGER',
            help=(
                f'Number of records to process per batch. '
                f'Default: {DEFAULT_BATCH_SIZE}. '
                f'Larger values may improve throughput but use more memory.'
            ),
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            default=False,
            help=(
                'Run the migration without making any database changes. '
                'Useful for validating data and estimating migration size.'
            ),
        )
        parser.add_argument(
            '--start-from',
            type=str,
            default=None,
            metavar='STRING',
            help=(
                'Resume migration from a specific external_id (inclusive). '
                'Useful for resuming an interrupted migration. '
                'Example: --start-from legacy-50001'
            ),
        )

    def handle(self, *args, **options):
        batch_size = options['batch_size']
        dry_run = options['dry_run']
        start_from = options['start_from']

        if batch_size < 1:
            raise CommandError('--batch-size must be a positive integer.')

        # Start memory tracking
        tracemalloc.start()

        # Start timing
        start_time = time.perf_counter()

        self.stdout.write(self.style.HTTP_INFO('=' * 60))
        self.stdout.write(self.style.HTTP_INFO('  Django ETL Pipeline: migrate_orders'))
        self.stdout.write(self.style.HTTP_INFO('=' * 60))
        self.stdout.write(f'  Batch size    : {batch_size:,}')
        self.stdout.write(f'  Dry run       : {dry_run}')
        self.stdout.write(f'  Start from    : {start_from or "(beginning)"}')
        self.stdout.write(self.style.HTTP_INFO('=' * 60))

        if dry_run:
            self.stdout.write(
                self.style.WARNING('\n[DRY RUN] No changes will be made to the database.\n')
            )

        # Build the base queryset — only unprocessed records, ordered for resumability
        queryset = (
            LegacyOrder.objects
            .filter(migrated=False)
            .order_by('external_id')
            .values('id', 'external_id', 'raw_data')
        )

        if start_from:
            queryset = queryset.filter(external_id__gte=start_from)
            self.stdout.write(f'Resuming from external_id >= "{start_from}"')

        # Count pending records (uses an efficient COUNT query)
        pending_count = queryset.count()
        self.stdout.write(f'Pending records to migrate: {pending_count:,}\n')

        if pending_count == 0:
            self.stdout.write(
                self.style.SUCCESS('Nothing to migrate. All records are already processed.')
            )
            tracemalloc.stop()
            return

        # Main processing loop
        orders_to_create = []
        lines_to_create = []
        processed_ids = []
        total_processed = 0
        total_batches = 0

        for legacy_row in queryset.iterator(chunk_size=batch_size):
            try:
                new_order, new_lines = self._transform(legacy_row)
            except (KeyError, ValueError, TypeError) as exc:
                self.stdout.write(
                    self.style.ERROR(
                        f'  [SKIP] Failed to transform record '
                        f'{legacy_row["external_id"]}: {exc}'
                    )
                )
                continue

            orders_to_create.append(new_order)
            lines_to_create.extend(new_lines)
            processed_ids.append(legacy_row['id'])

            if len(orders_to_create) >= batch_size:
                self._process_batch(
                    orders_to_create, lines_to_create, processed_ids, dry_run
                )
                total_processed += len(orders_to_create)
                total_batches += 1
                orders_to_create = []
                lines_to_create = []
                processed_ids = []

        # Process the final partial batch
        if orders_to_create:
            self._process_batch(
                orders_to_create, lines_to_create, processed_ids, dry_run
            )
            total_processed += len(orders_to_create)
            total_batches += 1

        # Final report
        elapsed = time.perf_counter() - start_time
        throughput = total_processed / elapsed if elapsed > 0 else 0

        # Memory snapshot
        snapshot = tracemalloc.take_snapshot()
        tracemalloc.stop()
        top_stats = snapshot.statistics('lineno')
        peak_memory_mb = sum(stat.size for stat in top_stats) / (1024 * 1024)

        self.stdout.write('')
        self.stdout.write(self.style.HTTP_INFO('=' * 60))
        self.stdout.write(self.style.SUCCESS('  Migration Complete'))
        self.stdout.write(self.style.HTTP_INFO('=' * 60))
        self.stdout.write(f'  Total records processed : {total_processed:,}')
        self.stdout.write(f'  Total batches           : {total_batches:,}')
        self.stdout.write(f'  Total time              : {elapsed:.2f} seconds')
        self.stdout.write(f'  Throughput              : {throughput:,.1f} records per second')
        self.stdout.write(f'  Peak memory usage       : {peak_memory_mb:.2f} MB')
        if dry_run:
            self.stdout.write(self.style.WARNING('  [DRY RUN] No changes were persisted.'))
        self.stdout.write(self.style.HTTP_INFO('=' * 60))

    def _transform(self, legacy_row: dict) -> tuple:
        """
        Transform a legacy order row dict into an (Order, [OrderLine]) tuple.

        Args:
            legacy_row: dict with keys 'id', 'external_id', 'raw_data'

        Returns:
            Tuple of (unsaved Order instance, list of unsaved OrderLine instances)

        Raises:
            KeyError: if required fields are missing from raw_data
            ValueError: if field values cannot be parsed
        """
        raw = legacy_row['raw_data']
        external_id = legacy_row['external_id']

        customer_email = raw['customer_email']
        total_price = raw['total']
        items = raw['items']

        # Create unsaved Order instance; store external_id for later PK linking
        new_order = Order(
            external_id=external_id,
            customer_email=customer_email,
            total_price=total_price,
        )
        # Temporarily attach external_id to allow line linkage after bulk_create
        new_order._external_id_temp = external_id

        new_lines = []
        for item in items:
            line = OrderLine(
                order=None,  # will be set after bulk_create
                sku=item['sku'],
                quantity=int(item['quantity']),
                unit_price=item['unit_price'],
            )
            # Temporarily track which order this line belongs to
            line._order_external_id = external_id
            new_lines.append(line)

        return new_order, new_lines

    def _process_batch(
        self,
        orders: list,
        lines: list,
        legacy_ids: list,
        dry_run: bool,
    ) -> None:
        """
        Persist a batch of transformed records atomically.

        Steps:
        1. bulk_create Orders
        2. Re-fetch created Orders to get their PKs
        3. Link OrderLines to their parent Orders
        4. bulk_create OrderLines
        5. Mark LegacyOrders as migrated

        All operations are wrapped in a single atomic transaction.
        If any step fails, the entire batch is rolled back.
        """
        if dry_run:
            self.stdout.write(
                f'[Dry Run] Would process {len(orders):,} records '
                f'({len(lines):,} order lines).'
            )
            return

        try:
            with transaction.atomic():
                # Step 1: Bulk insert Orders, skipping any rows that already exist
                Order.objects.bulk_create(orders, ignore_conflicts=True)

                # Step 2: Re-fetch to get PKs, keyed by external_id
                external_ids = [o._external_id_temp for o in orders]
                created_orders_map = Order.objects.filter(
                    external_id__in=external_ids
                ).in_bulk(field_name='external_id')

                # Step 3: Link OrderLines to their parent Order PKs
                for line in lines:
                    parent_order = created_orders_map.get(line._order_external_id)
                    if parent_order is None:
                        raise ValueError(
                            f'Could not find created Order for external_id='
                            f'{line._order_external_id}'
                        )
                    line.order = parent_order

                # Step 4: Bulk insert OrderLines, skipping duplicate line items
                OrderLine.objects.bulk_create(lines, ignore_conflicts=True)

                # Step 5: Mark legacy orders as migrated (single UPDATE query)
                updated = LegacyOrder.objects.filter(id__in=legacy_ids).update(migrated=True)

            self.stdout.write(
                self.style.SUCCESS(
                    f'Successfully processed batch of {len(orders):,} records '
                    f'({len(lines):,} lines, {updated} legacy records marked migrated).'
                )
            )

        except Exception as exc:
            self.stdout.write(
                self.style.ERROR(
                    f'Batch failed and was rolled back. Error: {exc}'
                )
            )
            raise
