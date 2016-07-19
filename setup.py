from setuptools import setup, find_packages

setup(name='email_processor',
      version='0.0.0',
      description='Personal email processor',
      long_description='Personal email processor',
      author='Christopher Kotfila',
      author_email='kotfic@gmail.com',
      url='',
      py_modules=['email_processor'],
      install_requires=[],
      license='Apache 2.0',
      zip_safe=False,
      entry_points= {
          'console_scripts': ['eprocess=email_processor.command:main']
      },
      keywords='ansible')
