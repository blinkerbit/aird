from setuptools import setup, find_packages

install_requires = [
    "tornado>=6.5.1",
    "uvloop>=0.19.0; sys_platform == 'linux'",
    "ldap3>=2.9.1",
    "aiofiles>=23.0.0",
    "argon2-cffi>=23.1.0",
    "requests>=2.31.0",
    "chardet>=5.0.0,<6.0.0",
    "pyasn1>=0.6.2",
    "webauthn>=2.0.0",
]

extras_require = {
    "compress": ["zstandard>=0.22.0", "brotli>=1.1.0"],
}

setup(
    name="aird",
    version="0.4.25.dev0",
    packages=find_packages(),
    include_package_data=True,
    package_data={"aird": [
        "templates/*.html",
        "static/css/*.css",
        "static/css/**/*.css",
        "static/js/*.js",
        "static/js/**/*.js",
        "static/img/*",
        "static/img/**/*",
        "static/favicon.*",
    ]},
    entry_points={
        "console_scripts": [
            "aird=aird.main:main",
            "aird-cli=aird.cli.main:main",
        ],
    },
    install_requires=install_requires,
    extras_require=extras_require,
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
