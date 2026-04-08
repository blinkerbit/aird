import os
from setuptools import setup, find_packages

def read_requirements(filename):
    if not os.path.exists(filename):
        return []
    with open(filename, encoding="utf-8") as f:
        return [
            line.strip() 
            for line in f 
            if line.strip() and not line.startswith("#") and not line.startswith("-") and not line.startswith("pip==") and not line.startswith("setuptools==") and not line.startswith("wheel==") 
        ]

install_requires = read_requirements("requirements.txt")
test_requires = read_requirements("requirements-test.txt")
dev_requires = read_requirements("requirements-dev.txt")

setup(
    name="aird",
    version="0.4.17",
    packages=find_packages(),
    package_data={"aird": ["templates/*.html"]},
    entry_points={
        "console_scripts": [
            "aird=aird.main:main",
        ],
    },
    install_requires=install_requires,
    extras_require={
        "dev": dev_requires,
        "test": test_requires,
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
