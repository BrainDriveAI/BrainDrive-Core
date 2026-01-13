# Database Migration Management Commands

.PHONY: validate-migrations verify-db test-migrations fix-migration-conflict advanced-tests monitor-health recovery-status check-security

# Security pattern checks
check-security:
	@echo "🔒 Checking security patterns..."
	@python scripts/check_security_patterns.py

# Validate all migrations for conflicts
validate-migrations:
	@echo "🔍 Validating migrations..."
	@ python scripts/validate_migrations.py --verbose

# Verify database state matches models
verify-db:
	@echo "🔍 Verifying database state..."
	@ python scripts/verify_database_state.py --verbose

# Test migration up/down cycle
test-migrations:
	@echo "🧪 Testing migrations..."
	@ python scripts/test_migrations.py

# Test individual migrations
test-migrations-individual:
	@echo "🧪 Testing individual migrations..."
	@ python scripts/test_migrations.py --test-individual

# Advanced migration testing (Phase 3)
advanced-tests:
	@echo "🧪 Running advanced migration tests..."
	@ python scripts/advanced_migration_tests.py --verbose

# Advanced performance testing
performance-tests:
	@echo "⚡ Running performance tests..."
	@ python scripts/advanced_migration_tests.py --performance-only --verbose

# Monitor migration health
monitor-health:
	@echo "🏥 Checking migration health..."
	@ python scripts/migration_monitor.py --verbose

# Start continuous monitoring
monitor-continuous:
	@echo "🔄 Starting continuous monitoring..."
	@ python scripts/migration_monitor.py --continuous

# Recovery status and diagnostics
recovery-status:
	@echo "📊 Migration recovery status..."
	@ python scripts/migration_recovery.py status

# Diagnose migration issues
recovery-diagnose:
	@echo "🔍 Diagnosing migration issues..."
	@ python scripts/migration_recovery.py diagnose

# Create emergency backup
emergency-backup:
	@echo "💾 Creating emergency backup..."
	@ python scripts/migration_recovery.py backup --name emergency_$(shell date +%Y%m%d_%H%M%S)

# List available backups
list-backups:
	@echo "📦 Available backups:"
	@ python scripts/migration_recovery.py list-backups

# Validate recovery state
validate-recovery:
	@echo "✅ Validating recovery state..."
	@ python scripts/migration_recovery.py validate

# Show migration dependency graph
migration-graph:
	@echo "📊 Migration dependency graph:"
	@ python scripts/validate_migrations.py --graph

# Fix current migration conflict (backup database first!)
fix-migration-conflict:
	@echo "🔧 Applying migration conflict fix..."
	@echo "⚠️  Make sure you have a database backup!"
	@read -p "Continue? (y/N): " confirm && [ "$$confirm" = "y" ]
	@ alembic upgrade head

# Create new migration with validation
new-migration:
	@echo "📝 Creating new migration..."
	@read -p "Migration description: " desc && \
	cd backend && \
	alembic revision --autogenerate -m "$$desc" && \
	echo "✅ Migration created. Running validation..." && \
	python scripts/validate_migrations.py

# Setup pre-commit hooks
setup-hooks:
	@echo "🔧 Setting up pre-commit hooks..."
	@pip install pre-commit
	@pre-commit install
	@echo "✅ Pre-commit hooks installed"

# Emergency: Reset migrations (DANGEROUS - only for development)
reset-migrations:
	@echo "⚠️  WARNING: This will reset all migrations!"
	@echo "⚠️  Only use in development with backed up data!"
	@read -p "Are you absolutely sure? Type 'RESET' to continue: " confirm && [ "$$confirm" = "RESET" ]
	@ \
	rm -rf migrations/versions/*.py && \
	alembic revision --autogenerate -m "reset_baseline" && \
	echo "✅ Migrations reset. Review the new baseline migration before applying."

# Help
help:
	@echo "Database Migration Management Commands:"
	@echo ""
	@echo "Phase 1 & 2 Commands:"
	@echo "  validate-migrations     - Check for migration conflicts"
	@echo "  verify-db              - Verify database matches models"
	@echo "  test-migrations        - Test migration up/down cycle"
	@echo "  test-migrations-individual - Test each migration individually"
	@echo "  migration-graph        - Show migration dependency graph"
	@echo "  fix-migration-conflict - Apply fix for current conflict"
	@echo "  new-migration          - Create new migration with validation"
	@echo "  setup-hooks           - Install pre-commit hooks"
	@echo ""
	@echo "Phase 3 Commands (Testing & Monitoring):"
	@echo "  advanced-tests         - Run comprehensive migration tests"
	@echo "  performance-tests      - Run migration performance tests"
	@echo "  monitor-health         - Check migration system health"
	@echo "  monitor-continuous     - Start continuous health monitoring"
	@echo "  recovery-status        - Show migration recovery status"
	@echo "  recovery-diagnose      - Diagnose migration issues"
	@echo "  emergency-backup       - Create emergency database backup"
	@echo "  list-backups          - List available backups"
	@echo "  validate-recovery      - Validate recovery state"
	@echo ""
	@echo "Emergency Commands:"
	@echo "  reset-migrations      - Reset all migrations (DANGEROUS)"
	@echo "  help                  - Show this help"