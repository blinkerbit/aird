from setuptools import setup, find_packages

# Try to import Rust extension support
try:
    from setuptools_rust import Binding, RustExtension
    rust_extensions = [
        RustExtension(
            "aird.rust_core",
            path="aird/rust_core/Cargo.toml",
            binding=Binding.PyO3,
            debug=False,
        )
    ]
    rust_dependencies = ['setuptools-rust>=1.7.0']
except ImportError:
    rust_extensions = []
    rust_dependencies = []

setup(
    name='aird',
    version="0.4.1",
    packages=find_packages(),
    package_data={'aird': ['templates/*.html']},
    rust_extensions=rust_extensions,
    entry_points={
        'console_scripts': [
            'aird=aird.main:main',
        ],
    },
    install_requires=[
        'tornado>=6.5.1',
        'ldap3>=2.9.1',
        'aiofiles>=23.0.0',
    ] + rust_dependencies,
    extras_require={
        'test': [
            'pytest>=8.3.3',
            'pytest-asyncio>=0.25.0',
            'pytest-mock>=3.14.0',
            'pytest-cov>=6.0.0',
            'pytest-tornado>=0.8.1',
            'coverage>=7.6.9',
            'mock>=5.1.0',
        ],
    },
    author='Viswantha Srinivas P',
    author_email='psviswanatha@gmail.com',  # Please fill this in
    description='Aird - A lightweight web-based file browser, editor, and streamer with real-time capabilities',
    url='https://github.com/blinkerbit/aird',
    long_description=open('README.md',encoding="utf-8").read(),
    long_description_content_type='text/markdown',
    license='Custom',
    classifiers=[
        'Programming Language :: Python :: 3',
        'License :: Other/Proprietary License',
        'Operating System :: OS Independent',
    ],
    python_requires='>=3.10',
    zip_safe=False,  # Required for Rust extensions
)














