#!/usr/bin/env bash
# scripts/test_railway_setup.sh
# Validates railway.toml files before pushing

set -e

echo "=== Railway Config Validation ==="

for f in "backend/railway.toml" "frontend/railway.toml"; do
    if [ ! -f "$f" ]; then
        echo "❌ Missing: $f"
        exit 1
    fi
done

python3 -c "
import sys

try:
    import tomllib
except ImportError:
    try:
        import tomli as tomllib
    except ImportError:
        print('⚠️  tomllib/tomli not available — skipping TOML validation')
        sys.exit(0)

required_deploy_fields = ['healthcheckPath', 'healthcheckTimeout']
errors = []

for path in ['backend/railway.toml', 'frontend/railway.toml']:
    try:
        with open(path, 'rb') as f:
            config = tomllib.load(f)

        assert 'build' in config, f'{path}: missing [build] section'
        assert 'deploy' in config, f'{path}: missing [deploy] section'

        deploy = config['deploy']
        for field in required_deploy_fields:
            assert field in deploy, f'{path}: missing deploy.{field}'

        print(f'✅ {path}: valid')
        print(f'   healthcheckPath: {deploy[\"healthcheckPath\"]}')
        print(f'   healthcheckTimeout: {deploy[\"healthcheckTimeout\"]}')

    except Exception as e:
        print(f'❌ {path}: {e}')
        errors.append(str(e))

if errors:
    sys.exit(1)
"

echo ""
echo "Checking for hardcoded secrets in railway.toml files..."
if grep -rE "(sk-|ls__|OPENAI_API_KEY\s*=\s*\S)" backend/railway.toml frontend/railway.toml 2>/dev/null; then
    echo "❌ Hardcoded secrets found in railway.toml!"
    exit 1
else
    echo "✅ No hardcoded secrets detected"
fi

echo ""
echo "CI/CD secrets required (set in GitHub → Settings → Secrets):"
if [ -f ".github/workflows/ci.yml" ]; then
    grep -o "secrets\.[A-Z_]*" .github/workflows/ci.yml | sort -u | while read secret; do
        echo "  - $secret"
    done
fi

echo ""
echo "=== Validation complete ==="