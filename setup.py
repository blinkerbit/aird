from setuptools import setup, find_packages

setup(
    name="aird",
    version="0.4.14",
    packages=find_packages(),
    package_data={"aird": ["templates/*.html"]},
    entry_points={
        "console_scripts": [
            "aird=aird.main:main",
        ],
    },
    install_requires=[
        "tornado>=6.5.1",
        "ldap3>=2.10.2rc3",
        "aiofiles>=23.0.0",
        "argon2-cffi>=23.1.0",
        "requests>=2.31.0",
        "chardet>=5.0.0,<6.0.0",
        'pysmbserver>=0.1.0; python_version>="3.13"',
        "wsgidav>=4.3.0",
        "cheroot>=10.0.0",
        "pyasn1>=0.6.2",
    ],
    extras_require={
        "dev": [
            "ruff>=0.15.0",
            "black>=24.0.0",
        ],
        "test": [
            "pytest>=8.3.3",
            "pytest-asyncio>=0.25.0",
            "pytest-mock>=3.14.0",
            "pytest-cov>=6.0.0",
            "pytest-tornado>=0.8.1",
            "pytest-xdist>=3.5.0",
            "coverage>=7.6.9",
            "mock>=5.1.0",
        ],
    },
    author="Viswantha Srinivas P",
    author_email="psviswanatha@gmail.com",  # Please fill this in
    description="Aird - A lightweight web-based file browser, editor, and streamer with real-time capabilities",
    url="https://github.com/blinkerbit/aird",
    long_description=open("README.md", encoding="utf-8").read(),
    long_description_content_type="text/markdown",
    license="Custom",
    classifiers=[
        "Programming Language :: Python :: 3",
        "License :: Other/Proprietary License",
        "Operating System :: OS Independent",
    ],
    python_requires=">=3.10",
)
