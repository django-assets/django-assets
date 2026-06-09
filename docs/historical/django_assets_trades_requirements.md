# Trade and Tagging System Requirements for django_assets_trades

## Overview

This document specifies the requirements for `django_assets_trades`, an **optional Django app** shipped in the `django-assets` PyPI distribution. It extends `django_assets_core` with trade management and tagging capabilities, enabling flexible categorization and hierarchical organization of trades.

**PyPI distribution**: `django-assets` (single install ships core, brokerage, and trades apps; see ADR-0015)
**Django app**: `django_assets_trades`
**Depends on**: `django_assets_core` (also in the same distribution)

### App Structure

`django_assets_trades` is an **optional Django app** for use alongside `django_assets_core`. It provides:

- Trade management models and functionality
- User-defined tagging system for trades
- Trade P/L tracking and reporting
- Hierarchical trade relationships

The app is **completely optional** — `django_assets_core` can be used without it. When enabled in `INSTALLED_APPS`, it extends the core app by:

- Adding a `trade` ForeignKey to the `Transaction` model (via migration)
- Providing new models: `Trade`, `TagCategory`, `Tag`, `TradeTag`
- Extending querysets with trade-related filtering methods
- Providing trade management utilities and functions

### Key Features

- **Trades as first-class models**: Trade is a core model that transactions belong to (via ForeignKey)
- **Hierarchical trades**: Trades can have parent trades to form logical chains where one trade leads to another. Child trade P/L is aggregated into parent trade P/L.
- **User-defined key-value tagging**: TagCategory (key) + Tag (value) pairs attached to trades (not transactions). Users can define any tag categories they want (e.g., "Strategy", "Idea Origin", "Sector", "Tax Year", etc.)
- **Flat tags**: Tags are flat (no hierarchy) - all tags exist at the same level within their category. A trade can have an unlimited number of tags across any number of categories.
- **Queryable grouping**: Efficient queries to find all trades with specific tags or categories
- **Reporting support**: Group and report on trades by tags, categories, or trade hierarchies
- **Derived trade properties**: Instruments, status (open/closed), and dates are automatically derived from transactions (including child trades)

## Mission Statement

`django_assets_trades` provides **trade management and flexible tagging capabilities** that extend `django_assets_core` with hierarchical trade organization and user-defined categorization. The app enables developers to organize transactions into trades, track trade P/L, and categorize trades using flexible tag categories without modifying core models or functionality.

## Core Principles

* **Trades as first-class models**: Trade is a core model, not just a tag or metadata. Trades have their own identity, relationships, and derived properties.
* **Transaction belongs to single Trade**: A transaction logically belongs to one trade via ForeignKey relationship.
* **Tags apply to Trades, not Transactions**: Tags are for categorizing and organizing trades, not individual transactions.
* **Derived fields on Trade**: Instruments, status, dates, and position are computed properties, not stored fields, ensuring they always reflect current state.
* **Hierarchical trades**: Trades can have parent trades to form logical chains where one trade leads to another. Child trade P/L is aggregated into parent trade P/L.
* **Flat tags (no hierarchy)**: Tags are flat - all tags exist at the same level within their category. Trades provide hierarchy; tags provide categorization.
* **User-defined tag categories**: Users can create any tag categories they want (e.g., "Strategy", "Idea Origin", "Sector", "Tax Year", etc.). Categories are completely flexible and user-defined.

## Goals

### Trade Management

* **Trade model**: First-class model representing a trade that transactions belong to
* **Hierarchical trades**: Trades can have parent trades to form logical chains
* **Derived trade properties**: Instruments, status (open/closed), dates, and position automatically derived from transactions
* **Trade P/L tracking**: Calculate realized and unrealized P/L for trades, including child trade aggregation
* **Multi-account support**: Trades can span multiple accounts (open in one account, close in another)

### Tagging System

* **User-defined categories**: Users can create any tag categories they want (TagCategory model)
* **Flat tags**: Tags are flat (no hierarchy) - all tags exist at the same level within their category
* **Many-to-many relationship**: Trades can have multiple tags across different categories
* **Queryable grouping**: Efficient queries to find all trades with specific tags or categories
* **Tag filtering**: Support filtering trades by tags across different categories with AND/OR logic

### API Design

* **Trade queryset methods**: Filter trades by status, instrument, tags, hierarchy
* **Transaction queryset methods**: Filter transactions by trade
* **Trade instance methods**: Add/remove tags, get status, calculate P/L, get hierarchy
* **Tag management**: Convenience methods for creating and managing tags

### Integration

* **Django admin integration**: Register models, add inline editing, filters, autocomplete widgets
* **DRF serializers**: Support nested and flat representations, filtering by tags and hierarchy
* **Database requirements**: Indexes, constraints, migrations for all models

## Non-Goals

* Core ledger functionality (provided by `django_assets_core`)
* Market data or pricing services
* Tax calculation or lot-matching logic (FIFO/LIFO may be implemented separately)
* Order management or execution systems
* Tag hierarchy (tags are flat within categories)
* Default tag categories (users define their own)

## Target Users

* **Trading platform developers** building trade management systems
* **Portfolio managers** tracking trade performance and categorization
* **Data analysts** organizing and reporting on trades by custom categories
* **Developers** extending `django_assets_core` with trade-specific functionality

## Data Model

### Trade Model

**Purpose**: First-class model representing a trade that transactions belong to. Trades track position, status, can be tagged for categorization, and can have parent-child relationships to form logical trade chains.

**Fields Required**:
- `name`: Trade identifier/name (e.g., "2026 AAPL Purchase", "TSLA Short Jan 2026") - CharField, max 200 chars, indexed
- `parent`: Self-referential ForeignKey to Trade - optional, null/blank allowed, CASCADE on delete, related_name='children'
  - Allows trades to form hierarchical chains where one trade leads to another
  - Child trade P/L is included in parent trade P/L calculations
  - Must prevent circular hierarchies (document constraint requirement)
- `description`: Optional description - TextField, blank allowed
- `metadata`: Optional JSON field for custom extensions - JSONField, default empty dict

**Derived Fields** (computed properties, not stored in DB):
- `instruments`: Property that returns list of instruments involved in the trade (derived from transaction legs, including child trades)
- `status`: Property that returns "open" or "closed" (derived from net position calculation, including child trades)
- `open_date`: Property that returns datetime when trade was first opened (earliest transaction date when position went from 0 to non-zero, including child trades)
- `closed_date`: Property that returns datetime when trade was closed (latest transaction date when position went from non-zero to 0, including child trades), or None if still open
- `net_position`: Property that returns current net position quantity (sum of all transaction legs for trade's instruments, including child trades)
- `total_pnl`: Property that returns total P/L including all child trades (aggregated P/L)

**Constraints**: 
- `name` must be unique
- Must prevent circular trade hierarchies (document constraint requirement)

**Indexes Required**:
- `name` - for trade lookups
- `parent` - for hierarchical queries
- Composite index on `(parent, name)` - for hierarchical trade queries
- Consider index on computed status for filtering (may require denormalization or materialized view)

**Database**: Table name should follow project conventions (likely `django_assets_trades_trade` or `trades_trade`)

**Related Models**:
- `transactions`: Reverse ForeignKey from Transaction model (Transaction.trade)
- `tags`: Many-to-many relationship via TradeTag model
- `children`: Reverse ForeignKey from Trade model (Trade.parent)

### Transaction Model Updates

**New Field Required**:
- `trade`: ForeignKey to Trade - optional, null/blank allowed, SET_NULL on delete, related_name='transactions'
  - Allows transactions to belong to a single trade
  - Transaction can exist without a trade (null allowed)
  - If trade is deleted, transactions are preserved with trade=null

**Database**: Update existing Transaction table to add `trade_id` column

### TagCategory Model

**Purpose**: User-defined top-level categories for organizing tags. Users can create any categories they want (e.g., "Strategy", "Idea Origin", "Sector", "Tax Year", "Risk Level", etc.). Categories are completely flexible and user-defined.

**Fields Required**:
- `code`: Unique identifier (e.g., "strategy", "idea_origin", "sector", "tax_year") - CharField, max 50 chars, indexed
- `name`: Human-readable name (e.g., "Strategy", "Idea Origin", "Sector", "Tax Year") - CharField, max 100 chars
- `description`: Optional description - TextField, blank allowed
- `metadata`: Optional JSON field for custom extensions (colors, icons, UI metadata) - JSONField, default empty dict

**Constraints**: `code` must be unique

**Database**: Table name should follow project conventions (likely `django_assets_trades_tagcategory` or `trades_tagcategory`)

**Note**: No default categories are created - users define their own tag categories based on their needs

### Tag Model

**Purpose**: Individual tags belonging to a user-defined category. Tags are flat (no hierarchy) - all tags exist at the same level within their category. Users can create any tags they want within any category (e.g., "Long Stock" in "Strategy" category, "Dividend Newsletter" in "Idea Origin" category, "Technology" in "Sector" category, etc.).

**Fields Required**:
- `category`: ForeignKey to TagCategory - required, CASCADE on delete
- `name`: Tag name (e.g., "Long Stock", "Dividend Newsletter", "Technology", "2024") - CharField, max 100 chars
- `description`: Optional description - TextField, blank allowed
- `metadata`: Optional JSON field for custom extensions - JSONField, default empty dict

**Constraints**: 
- Unique together: `(category, name)` - prevents duplicate tags within a category

**Indexes Required**:
- `category` - for category filtering
- Composite index on `(category, name)` - for tag lookups

**Database**: Table name should follow project conventions (likely `django_assets_trades_tag` or `trades_tag`)

### TradeTag Model

**Purpose**: Many-to-many relationship between Trade and Tag

**Fields Required**:
- `trade`: ForeignKey to Trade - required, CASCADE on delete, related_name='tags'
- `tag`: ForeignKey to Tag - required, CASCADE on delete, related_name='trades`

**Constraints**: 
- Unique together: `(trade, tag)` - prevents duplicate tag assignments

**Indexes Required**:
- `(trade, tag)` - for reverse lookups and query performance
- Consider index on `tag` for tag-to-trades queries

**Database**: Table name should follow project conventions (likely `django_assets_trades_tradetag` or `trades_tradetag`)

## API Design

### Installation and Setup

**Installation**:
```bash
pip install django-assets
```

**Django Settings**:
```python
INSTALLED_APPS = [
    'django_assets_core',    # required
    'django_assets_trades',  # optional trade management and tagging
    # ... other apps
]
```

**Migrations**:
```bash
python manage.py migrate django_assets_trades
```

### Query API Requirements

#### Trade Queryset Methods

**Trade Queryset Methods** (to be added to Trade model):

- `Trade.objects.with_tag(category_code, tag_name)` - Filter trades by specific tag
- `Trade.objects.with_category(category_code)` - Filter trades by category
- `Trade.objects.with_tags(**tag_filters)` - Filter by multiple tags with AND/OR logic (see Tag Filtering section below)
- `Trade.objects.open()` - Filter to open trades (net_position != 0, including child trades)
- `Trade.objects.closed()` - Filter to closed trades (net_position == 0, including child trades)
- `Trade.objects.with_instrument(instrument)` - Filter trades that involve a specific instrument (including child trades)
- `Trade.objects.root_trades()` - Filter to root trades (trades with no parent)
- `Trade.objects.children_of(parent_trade)` - Filter to direct children of a parent trade
- `Trade.objects.descendants_of(trade)` - Filter to all descendants of a trade (recursive)
- `Trade.objects.ancestors_of(trade)` - Filter to all ancestors of a trade (recursive)

#### Transaction Queryset Methods

**Transaction Queryset Methods** (to be added to Transaction model):

- `Transaction.objects.for_trade(trade)` - Filter transactions belonging to a specific trade
- `Transaction.objects.for_trades(**trade_filters)` - Filter transactions belonging to trades matching filters

#### Tag Model Methods

**Tag Model Methods**:

- `Tag.get_or_create(category_code, tag_name)` - Convenience method to get or create tags (tags are flat, no parent needed)

#### Trade Instance Methods

**Trade Instance Methods**:

- `trade.add_tag(category_code, tag_name)` - Add a tag to trade (tags are flat, no parent needed)
- `trade.remove_tag(category_code, tag_name)` - Remove a tag from trade
- `trade.get_tags_by_category(category_code)` - Get all tags for trade in a specific category
- `trade.get_instruments()` - Get list of instruments involved in the trade (derived from transactions, including child trades)
- `trade.get_status()` - Get trade status ("open" or "closed") based on net position (including child trades)
- `trade.get_open_date()` - Get datetime when trade was first opened (derived, including child trades)
- `trade.get_closed_date()` - Get datetime when trade was closed, or None if still open (derived, including child trades)
- `trade.get_net_position()` - Get current net position quantity (derived from transactions, including child trades)
- `trade.get_transactions()` - Get all transactions belonging to this trade (via reverse ForeignKey)
- `trade.get_children()` - Get all child trades (trades that have this trade as parent)
- `trade.get_parent()` - Get parent trade, or None if this is a root trade
- `trade.get_ancestors()` - Get all parent trades up to root (all ancestors)
- `trade.get_descendants()` - Get all child trades recursively (all descendants)
- `trade.get_total_pnl()` - Get total P/L including all child trades (aggregated P/L)

### Tag-to-Tag Filtering Requirements

The system must support filtering trades by tags across different categories with AND/OR logic. Tag filtering returns matching trades.

#### Multi-Tag Filtering API

**TagFilter Class/Helper** (for building complex tag queries):

- `TagFilter()` - Create a tag filter builder
- `TagFilter.include(category_code, tag_names, logic='OR')` - Include trades with any/all of specified tags
  - `tag_names`: List of tag names or single tag name
  - `logic`: 'OR' (default) or 'AND' - if multiple tag_names, use OR or AND between them
- `TagFilter.exclude(category_code, tag_names, logic='OR')` - Exclude trades with any/all of specified tags
- `TagFilter.and_filter(other_filter)` - Combine filters with AND logic
- `TagFilter.or_filter(other_filter)` - Combine filters with OR logic

**Trade Queryset Methods for Tag Filtering**:

- `Trade.objects.with_tags(**tag_filters)` - Filter by multiple tag categories
  - `tag_filters`: Dict where keys are category codes (user-defined), values are tag names or lists
  - Example: `with_tags(strategy='Long Stock', idea_origin='Dividend Newsletter')` - AND logic between categories
  - Note: Category codes are user-defined - users can create any categories they want
- `Trade.objects.with_tags_any(category_code, tag_names)` - Filter by any tag in list (OR within category)
- `Trade.objects.with_tags_all(category_code, tag_names)` - Filter by all tags in list (AND within category)
- `Trade.objects.with_tag_filter(tag_filter)` - Apply TagFilter object to queryset

**Tag Filter Examples** (using user-defined categories):

```python
from django_assets_trades.models import Trade

# Example: Find trades tagged with "Long Stock" in "Strategy" category AND "Dividend Newsletter" in "Idea Origin" category
# Note: Category names are user-defined - users can create any categories they want
Trade.objects.with_tags(
    strategy='Long Stock',
    idea_origin='Dividend Newsletter'
)

# Example: Find trades tagged with "Long Stock" OR "Covered Call" in "Strategy" category
Trade.objects.with_tags_any('strategy', ['Long Stock', 'Covered Call'])

# Example: Find trades tagged with "Long Stock" in "Strategy" category AND 
# ("Dividend Newsletter" OR "@stockguru123 Twitter") in "Idea Origin" category
filter1 = TagFilter().include('strategy', 'Long Stock')
filter2 = TagFilter().include('idea_origin', ['Dividend Newsletter', '@stockguru123 Twitter'], logic='OR')
combined = filter1.and_filter(filter2)
Trade.objects.with_tag_filter(combined)

# Example: Exclude trades with "Dividend Newsletter" tag in "Idea Origin" category
Trade.objects.exclude(tags__category__code='idea_origin', tags__name='Dividend Newsletter')

# Example: Users can create any categories - e.g., "Sector", "Tax Year", "Risk Level", etc.
Trade.objects.with_tags(sector='Technology', tax_year='2024')
```

#### Tag Filtering Logic Requirements

**Tag Filtering Principle**:

- Tag filtering returns trades that match the filter criteria
- Filtering is about finding trades that have specific tag combinations
- Trades can be filtered directly by their tags

**AND Logic Between Categories**:

- When filtering by multiple categories, use AND logic by default
- Example: `Trade.objects.with_tags(strategy='Long Stock', idea_origin='Dividend Newsletter')` means:
  - Find trades that have BOTH:
    - Tag "Long Stock" in user-defined "Strategy" category AND
    - Tag "Dividend Newsletter" in user-defined "Idea Origin" category
  - Returns: Trades that match this criteria
  - Note: Category names are user-defined - users can create any categories they want

**OR Logic Within Category**:

- When providing multiple tags for same category, support OR logic
- Example: `Trade.objects.with_tags_any('strategy', ['Long Stock', 'Covered Call'])` means:
  - Find trades that have EITHER:
    - Tag "Long Stock" OR "Covered Call" in user-defined "Strategy" category
  - Returns: Trades that match this criteria
  - Note: Category names are user-defined - users can create any categories they want

**AND Logic Within Category**:

- Support AND logic within category for cases where trades must have multiple tags
- Example: `Trade.objects.with_tags_all('strategy', ['Long Stock', 'High Volatility'])` means:
  - Find trades that have BOTH "Long Stock" AND "High Volatility" tags in user-defined "Strategy" category
  - Returns: Trades that match this criteria
  - Note: Category names are user-defined - users can create any categories they want

**Exclude Logic**:

- Exclude filters find trades that do NOT have excluded tags
- Example: Find trades excluding "Dividend Newsletter" tag in user-defined "Idea Origin" category while including "Long Stock" tag in user-defined "Strategy" category
- Note: Category names are user-defined - users can create any categories they want

**Getting Transactions from Filtered Trades**:

- After filtering trades, transactions are retrieved via the trade relationship:
```python
from django_assets_trades.models import Trade
from django_assets_core.models import Transaction

# Step 1: Filter trades by user-defined tags (example uses "Strategy" category)
matching_trades = Trade.objects.with_tags(strategy='Long Stock')

# Step 2: Get transactions that belong to those trades
transactions = Transaction.objects.filter(trade__in=matching_trades)

# Or filter by account as well
transactions = Transaction.objects.filter(
    account=my_account,
    trade__in=matching_trades
)
```

**Account Filtering Integration**:

- Account filtering can be done when getting transactions from filtered trades
- Example workflow (using user-defined "Strategy" category):

  1. Filter trades: `matching_trades = Trade.objects.with_tags(strategy='Long Stock')`
  2. Get transactions with account filter: `Transaction.objects.filter(account=my_account, trade__in=matching_trades)`

- Alternatively, filter trades by accounts involved:

  1. Filter trades: `matching_trades = Trade.objects.with_tags(strategy='Long Stock').filter(transactions__account=my_account).distinct()`
  2. Get transactions: `Transaction.objects.filter(trade__in=matching_trades)`

#### Tag Filtering Performance Requirements

- Tag filtering queries must be efficient (use appropriate JOINs, not N+1 queries)
- Support database-level filtering (not Python-level filtering)
- Use appropriate indexes for tag filtering queries
- Consider query optimization for complex tag filter combinations
- When getting transactions from filtered trades, use efficient JOINs to avoid N+1 problems
- Consider caching frequently filtered tag sets for performance
- Consider caching derived trade fields (status, position, dates) for performance

### Trade-Specific API Requirements

The system must provide built-in functions and model methods for trade management.

#### Trade Creation and Management Functions

**Trade Creation**:

- `create_trade(name, parent=None, description=None)` - Create a new trade
  - `name`: Required - unique trade identifier/name
  - `parent`: Optional parent Trade instance (or trade name/ID) - creates hierarchical trade relationship
  - `description`: Optional description
  - Returns: Trade instance
  - Note: Trade instruments are derived from transactions, not specified at creation
  - Note: Trade is NOT account-specific - a trade can span multiple accounts
  - Note: Child trade P/L is aggregated into parent trade P/L

**Transaction Assignment**:

- `assign_transaction_to_trade(transaction, trade)` - Assign a transaction to a trade
  - `transaction`: Transaction instance to assign
  - `trade`: Trade instance (or trade name/ID)
  - Updates `transaction.trade` ForeignKey
  - System automatically recalculates trade's derived fields (instruments, status, dates, position)
  - No manual "opening"/"closing" designation needed - determined by position changes
- `remove_transaction_from_trade(transaction)` - Remove transaction from its trade
  - Sets `transaction.trade = None`
  - System automatically recalculates trade's derived fields

**Trade Lookup**:

- `get_trade(name)` - Get trade by name
- `get_or_create_trade(name, description=None)` - Get or create trade by name

#### Trade Status Functions (Model Methods)

**Trade Status Properties** (on Trade model):

- `trade.status` - Property that returns "open" or "closed"
  - Logic: Trade is open if net position (across ALL accounts) is non-zero
  - Trade is closed when net position returns to zero
  - Position is calculated by summing all transaction legs for trade's instruments
- `trade.net_position` - Property that returns current net position quantity
  - Can be negative for short positions
  - Calculated from all transactions belonging to the trade
- `trade.open_date` - Property that returns datetime when trade was first opened
  - Earliest transaction date when position went from 0 to non-zero
  - Returns None if trade has never been opened
- `trade.closed_date` - Property that returns datetime when trade was closed
  - Latest transaction date when position went from non-zero to 0
  - Returns None if trade is still open
- `trade.instruments` - Property that returns list of instruments involved in the trade
  - Derived from all transaction legs across all transactions in the trade
  - Returns unique list of instruments

**Trade Status Methods**:

- `trade.get_status()` - Get detailed trade status
  - Returns: Dict with keys: 
    - `status`: "open" or "closed"
    - `net_position`: Current net position quantity
    - `instruments`: List of instruments involved
    - `opening_transactions`: List of transactions that opened the position (determined by position math)
    - `closing_transactions`: List of transactions that closed the position (determined by position math)
    - `adjustment_transactions`: List of transactions that adjusted but didn't open/close
    - `total_quantity_opened`: Total quantity opened across all opening transactions
    - `total_quantity_closed`: Total quantity closed across all closing transactions
    - `accounts_involved`: List of accounts that have transactions in this trade
    - `open_date`: Datetime when trade was first opened
    - `closed_date`: Datetime when trade was closed (None if still open)
  - Note: Opening/closing is determined automatically by tracking position changes

#### Trade P/L Functions

**Trade Profit/Loss Methods** (on Trade model):

- `trade.calculate_pnl(as_of=None)` - Calculate P/L for a trade
  - `as_of`: Optional datetime to calculate P/L as of a specific point in time
  - Returns: Dict with keys: `realized_pnl`, `unrealized_pnl`, `total_pnl`, `cost_basis`, `current_value`, `transactions_count`
  - Logic:
    - **Position tracking**: Trade tracks net position across ALL accounts
    - **Opening transactions**: Automatically identified as transactions that move trade position from 0 to non-zero
    - **Closing transactions**: Automatically identified as transactions that move trade position from non-zero to 0
    - **Realized P/L**: Calculated from closed positions (when position returned to zero)
      - Cost basis: Sum of all opening transaction values (including fees)
      - Sale proceeds: Sum of all closing transaction values (including fees)
      - Realized P/L = Sale proceeds - Cost basis
    - **Unrealized P/L**: Calculated from open positions (if trade is still open)
      - Cost basis: Sum of opening transaction values for remaining position
      - Current value: Current position × current price (requires price connector)
      - Unrealized P/L = Current value - Cost basis
  - **Multi-account support**: Trade P/L is calculated across all accounts that have transactions in the trade
- `trade.get_summary(as_of=None)` - Get comprehensive trade summary
  - Returns: Dict combining status and P/L information
  - Includes: status, P/L metrics, transaction list (with auto-determined opening/closing), dates, quantities, accounts involved, instruments

#### Trade Query Functions

**Trade Discovery and Filtering**:

- `get_all_trades(instrument=None, status=None, tag_filters=None, account_involved=None)` - Get all trades with optional filters
  - `instrument`: Filter by instrument (trades that involve this instrument)
  - `status`: Filter by "open" or "closed"
  - `tag_filters`: Dict of tag filters to apply (e.g., `{'strategy': 'Long Stock'}`)
    - Keys are user-defined category codes, values are tag names or lists
    - Filters trades by their tags
    - Returns trades that match the filter criteria
  - `account_involved`: Filter to trades that have transactions in this account (doesn't restrict trade to account)
  - Returns: QuerySet of Trade objects
- `get_open_trades(instrument=None, tag_filters=None, account_involved=None)` - Get all open trades with tag filtering
- `get_closed_trades(instrument=None, tag_filters=None, account_involved=None)` - Get all closed trades with tag filtering

**Trade Query Examples** (using user-defined categories):

```python
from django_assets_trades.models import Trade

# Example: Get all trades tagged with "Long Stock" in user-defined "Strategy" category
trades = Trade.objects.with_tags(strategy='Long Stock')

# Example: Get all open trades tagged with "Long Stock" in "Strategy" category
trades = Trade.objects.open().with_tags(strategy='Long Stock')

# Example: Get all open "Long Stock" trades in specific account
trades = Trade.objects.open().with_tags(strategy='Long Stock').filter(transactions__account=my_account).distinct()

# Example: Get trades with "Long Stock" OR "Covered Call" in "Strategy" category
trades = Trade.objects.with_tags_any('strategy', ['Long Stock', 'Covered Call'])

# Example: Get trades with "Long Stock" in "Strategy" category AND "Dividend Newsletter" in "Idea Origin" category
trades = Trade.objects.with_tags(strategy='Long Stock', idea_origin='Dividend Newsletter')

# Example: Users can create any categories - e.g., filter by "Sector" and "Tax Year"
trades = Trade.objects.with_tags(sector='Technology', tax_year='2024')

# Get trades involving specific instrument
trades = Trade.objects.with_instrument(aapl_instrument)
```

## Integration Requirements

### Django Admin Integration

- Register Trade, TagCategory, Tag, and TradeTag models in admin
- Add inline editing for TradeTag on Trade admin page
- Add `trade` field to Transaction admin with autocomplete widget
- Add filters for tags in Trade admin list view
- Add filters for trade in Transaction admin list view
- Add autocomplete widgets for tag selection in admin forms
- Support filtering trades by category and/or tag in admin
- Support filtering transactions by trade in admin
- Display derived fields (status, instruments, dates, position) in Trade admin

### DRF (Django REST Framework) Serializer Requirements

- Add `trade` field to TransactionSerializer (support both nested and flat representations)
- Add `tags` field to TradeSerializer (support both nested and flat representations)
- Add `parent` and `children` fields to TradeSerializer (support hierarchical trade representation)
- Add derived fields to TradeSerializer: `status`, `instruments`, `open_date`, `closed_date`, `net_position`, `total_pnl`
- Support filtering trades by tags in API views (query parameters)
- Support filtering transactions by trade in API views (query parameters)
- Support filtering trades by parent/child relationships in API views
- Create TagCategorySerializer for tag category management
- Create TagSerializer for tag management (tags are flat, no parent/children)

## Database Requirements

### Index Requirements

- Index on `Trade.name` for trade lookups
- Index on `Trade.parent` for hierarchical trade queries
- Composite index on `Trade(parent, name)` for hierarchical trade queries
- Index on `Transaction.trade` for filtering transactions by trade
- Index on `TagCategory.code` for category lookups
- Composite index on `Tag(category, name)` for tag lookups
- Index on `Tag.category` for category filtering
- Composite index on `TradeTag(trade, tag)` for reverse lookups
- Consider index on `TradeTag.tag` for tag-to-trades queries
- Consider composite indexes for multi-tag filtering performance (e.g., `(trade_id, tag_id, category_id)`)
- Consider index on `Transaction.trade` for efficient trade transaction queries

### Constraint Requirements

- Unique constraint on `Trade.name`
- Foreign key constraint on `Transaction.trade` to `Trade` (SET_NULL on delete)
- Foreign key constraint on `Trade.parent` to `Trade` (CASCADE on delete)
- Unique constraint on `TagCategory.code`
- Unique constraint on `Tag(category, name)` - tags are flat, no parent field
- Unique constraint on `TradeTag(trade, tag)`
- Consider database-level constraint to prevent circular trade hierarchies (document as optional requirement)

### Migration Requirements

- Initial migration must create Trade model
- Migration must add `trade` ForeignKey to Transaction model (nullable, SET_NULL on delete)
  - **Note**: This migration extends the core `Transaction` model. The trades app should use Django's model extension patterns to add the ForeignKey field without modifying core models directly.
- Initial migration must create TagCategory, Tag, and TradeTag models
- Migration must include all indexes
- Migration must include all constraints
- Migration should follow project naming conventions
- Migration must handle existing transactions (set trade=None for existing records)
- No default "Trades" category needed since trades are now a core model

## Testing Scope

- Unit tests for trade model methods (status, P/L calculation, hierarchy)
- Unit tests for tag filtering queries (AND/OR logic, multi-category filtering)
- Integration tests for trade creation and transaction assignment
- Integration tests for tag creation and assignment
- Integration tests for hierarchical trade relationships
- Integration tests for trade P/L calculation across multiple accounts
- Integration tests for derived field calculations (instruments, status, dates, position)
- Performance tests for tag filtering queries
- Database constraint tests (unique constraints, foreign keys, circular hierarchy prevention)

## Documentation Scope

- **Getting started:** install `django_assets_trades`, basic usage
- **Trade management:** creating trades, assigning transactions, understanding derived fields
- **Tagging system:** creating categories and tags, tagging trades, querying by tags
- **Hierarchical trades:** creating parent-child relationships, understanding P/L aggregation
- **API reference:** complete documentation for all model methods and queryset methods
- **Examples:** common workflows (trade tracking, tag-based reporting, hierarchical organization)
- **Integration:** Django admin setup, DRF serializer usage
- **Best practices:** trade organization patterns, tag category design, performance considerations

## Packaging & Deployment

- PyPI distribution: `django-assets` (single distribution ships `django_assets_core`, `django_assets_brokerage`, and `django_assets_trades` as Django apps; see ADR-0015)
- Django app label: `django_assets_trades`
- Requires the `django_assets_core` app (also enabled in `INSTALLED_APPS`)
- Tested on **PostgreSQL ≥ 12**, **Django ≥ 4.2 LTS**, **Python ≥ 3.11**
- Licensed under MIT

## Roadmap

| Milestone | Highlights |
| ------------ | ------------------------------------------------------------------------------------------ |
| **v0.1 MVP** | Core Trade model, Transaction.trade ForeignKey, basic tag system (TagCategory, Tag, TradeTag) |
| **v0.2** | Hierarchical trades (parent-child relationships), tag filtering queries |
| **v0.3** | Trade P/L calculation, derived fields (status, dates, position) |
| **v0.4** | Django admin integration, DRF serializers |
| **v0.5+** | Performance optimizations, caching, advanced reporting features |

## Success Criteria

- All trades can be created and managed through the API
- Transactions can be assigned to trades via ForeignKey
- Tags can be created and assigned to trades with user-defined categories
- Trade status, dates, and position are correctly derived from transactions
- Trade P/L is calculated correctly, including child trade aggregation
- Tag filtering queries work efficiently with AND/OR logic
- Hierarchical trade relationships work correctly (parent-child)
- All database constraints are enforced
- Django admin and DRF integration work seamlessly

## Relationship to django_assets_core

`django_assets_trades` is an **optional Django app** that runs alongside `django_assets_core` (both ship in the `django-assets` PyPI distribution). The trades app:

- **Extends** the core app with trade management and tagging capabilities
- **Adds** a `trade` ForeignKey to the `Transaction` model (via migration)
- **Does not modify** other core models or functionality
- **Can be enabled/disabled** in `INSTALLED_APPS` without affecting core functionality (transactions without trades remain valid)

Developers can:

1. **Use trades**: enable `django_assets_trades` in `INSTALLED_APPS` and organize transactions into trades
2. **Use tags**: create custom tag categories and tag trades for flexible categorization
3. **Build custom extensions**: use trade and tag models as building blocks for custom functionality

The core app provides all the storage and integrity guarantees; this trades app provides trade organization and categorization capabilities.

## Example Use Cases

### Use Case 1: Trade Tracking

- Use `create_trade()` to create "2026 AAPL Purchase" trade
- Use `assign_transaction_to_trade()` to assign opening transaction (buy 100 shares in Account A)
  - System automatically identifies this as opening transaction (position goes from 0 to 100)
  - Trade's `instruments` property now includes AAPL
  - Trade's `status` becomes "open"
  - Trade's `open_date` is set to transaction date
- Use `assign_transaction_to_trade()` to assign closing transaction (sell 100 shares in Account B)
  - System automatically identifies this as closing transaction (position goes from 100 to 0)
  - Trade's `status` becomes "closed"
  - Trade's `closed_date` is set to transaction date
- Use `trade.status` to check if trade is closed (returns "closed", position is 0)
- Use `trade.calculate_pnl()` to get P/L (cost basis from Account A, proceeds from Account B)
- Use `trade.get_summary()` for complete trade overview (shows opening/closing transactions, accounts involved, P/L)

### Use Case 2: User-Defined "Strategy" Category

- **Example**: User creates a "Strategy" category (users can create any categories they want)
- Tags in "Strategy" category: "Long Stock", "Short Stock", "Covered Call", "Hedging", "Short Put", "Long Put", etc.
- Tag trades with strategy tags
- Filter trades by strategy: 
  ```python
  from django_assets_trades.models import Trade
  
  # Filter trades tagged with "Long Stock" in user-defined "Strategy" category
  trades = Trade.objects.with_tags(strategy='Long Stock')
  ```
- Get transactions from filtered trades:
  ```python
  # Get transactions that belong to filtered trades
  transactions = Transaction.objects.filter(trade__in=trades)
  ```
- Report on P&L by strategy using tag filtering
- Combine strategy filters with account filters:
  ```python
  # Get "Long Stock" trades that have transactions in specific account
  trades = Trade.objects.with_tags(strategy='Long Stock').filter(transactions__account=my_account).distinct()
  transactions = Transaction.objects.filter(trade__in=trades, account=my_account)
  ```

### Use Case 3: User-Defined "Idea Origin" Category with Multi-Tag Filtering

- **Example**: User creates an "Idea Origin" category (users can create any categories they want)
- Tags in "Idea Origin" category: "Dividend Newsletter", "@stockguru123 Twitter", "Research Report", "Friend Recommendation"
- Tag trades with idea origin tags
- Filter trades by idea origin: 
  ```python
  from django_assets_trades.models import Trade
  
  trades = Trade.objects.with_tags(idea_origin='Dividend Newsletter')
  ```
- Filter trades by multiple user-defined categories:
  ```python
  # Find trades that have tags in both "Strategy" and "Idea Origin" categories
  trades = Trade.objects.with_tags(strategy='Long Stock', idea_origin='Dividend Newsletter')
  # Then get transactions
  transactions = Transaction.objects.filter(trade__in=trades)
  ```
- Report on P/L by idea origin using tag filtering
- Combine multiple filters: 
  ```python
  # Filter trades with multiple tag criteria across different user-defined categories
  trades = Trade.objects.with_tags(strategy='Long Stock', idea_origin='Dividend Newsletter')
  # Get transactions for specific account
  transactions = Transaction.objects.filter(trade__in=trades, account=my_account)
  ```

### Use Case 4: Hierarchical Trades - Logical Trade Chains

- Create parent trade: "2026 AAPL Strategy"
- Create child trade: "2026 AAPL Initial Entry" with parent="2026 AAPL Strategy"
  - Assign opening transaction (buy 100 shares)
  - Child trade has its own P/L
- Create another child trade: "2026 AAPL Follow-up" with parent="2026 AAPL Strategy"
  - Assign adjustment transaction (buy 50 more shares)
  - Child trade has its own P/L
- Parent trade's `total_pnl` includes both child trades' P/L
  ```python
  from django_assets_trades.models import Trade
  
  # Get parent trade
  parent_trade = Trade.objects.get(name="2026 AAPL Strategy")
  
  # Get parent's own P/L
  parent_pnl = parent_trade.calculate_pnl()
  
  # Get parent's total P/L (including all child trades)
  total_pnl = parent_trade.get_total_pnl()
  
  # Get all child trades
  children = parent_trade.get_children()
  
  # Get all descendants (children and their children, recursively)
  descendants = parent_trade.get_descendants()
  ```
- Query root trades (trades with no parent):
  ```python
  from django_assets_trades.models import Trade
  
  root_trades = Trade.objects.root_trades()
  ```
- Report on P/L by trade hierarchy:
  ```python
  from django_assets_trades.models import Trade
  
  # Get all root trades and their aggregated P/L
  for root in Trade.objects.root_trades():
      print(f"{root.name}: {root.get_total_pnl()}")
      for child in root.get_children():
          print(f"  {child.name}: {child.get_total_pnl()}")
  ```
