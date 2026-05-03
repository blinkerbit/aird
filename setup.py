from setuptools import setup, find_packages

install_requires = [
    "tornado>=6.5.1",
    "ldap3>=2.9.1",
    "aiofiles>=23.0.0",
    "argon2-cffi>=23.1.0",
    "requests>=2.31.0",
    "chardet>=5.0.0,<6.0.0",
    "pysmbserver>=0.1.0; python_version>='3.13'",
    "wsgidav>=4.3.0",
    "cheroot>=10.0.0",
    "pyasn1>=0.6.2",
]

setup(
    name="aird",
    version="0.4.21",
    packages=find_packages(),
    package_data={"aird": [
        "templates/*.html",
        "static/css/*.css",
        "static/js/*.js",
        "static/js/**/*.js",
    ]},
    entry_points={
        "console_scripts": [
            "aird=aird.main:main",
        ],
    },
    install_requires=install_requires,
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
