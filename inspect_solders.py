import importlib, os, solders as s

spec = importlib.util.find_spec('solders')
print('spec:', spec)
p = getattr(s, '__file__', None) or spec.origin
pkgdir = os.path.dirname(p)
print('package dir:', pkgdir)

for root, dirs, files in os.walk(pkgdir):
    print(root.replace(pkgdir, '.'), dirs, [f for f in files if f.endswith('.py')])
    break
