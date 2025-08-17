from setuptools import setup, find_packages

setup(
    name='aird',
    version="0.3.5",
    packages=find_packages(),
    package_data={'aird': ['templates/*.html']},
    entry_points={
        'console_scripts': [
            'aird=aird.main:main',
        ],
    },
    install_requires=[
        'tornado>=6.5.1',
        'ldap3>=2.9.1',
    ],
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
)






