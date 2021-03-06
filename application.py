import os
import json
import random
import string
import requests
import httplib2
from flask import Flask, render_template, request, redirect, jsonify, \
    url_for, flash, Markup, make_response, send_from_directory
from sqlalchemy import create_engine, asc
from sqlalchemy.orm import sessionmaker
from database_setup import Base, Category, Product, ProductPhoto, User
from flask import session as login_session
from flask_wtf.csrf import CSRFProtect
from werkzeug.utils import secure_filename
from oauth2client.client import flow_from_clientsecrets
from oauth2client.client import FlowExchangeError


app = Flask(__name__)

# Google client secrets
CLIENT_ID = json.loads(
    open('client_secrets.json', 'r').read())['web']['client_id']
APPLICATION_NAME = "Store Catalog Application"

# Connect to Database and create database session
engine = create_engine('postgresql+psycopg2:///mystore')
Base.metadata.bind = engine

DBSession = sessionmaker(bind=engine)
session = DBSession()

# Photo upload constants
ALLOWED_EXTENSIONS = set(['png', 'jpg', 'jpeg', 'gif'])
app.config['UPLOAD_FOLDER'] = './static/uploads'


########################################
# IMAGE FUNCTIONS
########################################

def allowed_file(filename):
    '''Determine if uploaded photo is correct file type'''
    return '.' in filename and \
           filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


@app.route('/uploads/<filename>')
def viewUploadFile(filename):
    '''View images'''
    print(app.config['UPLOAD_FOLDER'])
    return send_from_directory(app.config['UPLOAD_FOLDER'], filename)


########################################
# LOGIN / LOGOUT
########################################

@app.route('/login')
def showLogin():
    '''Create anti-forgery state token'''
    state = ''.join(random.choice(string.ascii_uppercase + string.digits)
                    for x in xrange(32))
    login_session['state'] = state
    # return "The current session state is %s" % login_session['state']
    return render_template('login.html', STATE=state)


# Google connect
@app.route('/gconnect', methods=['POST'])
def gconnect():
    # Validate state token
    if request.args.get('state') != login_session['state']:
        response = make_response(json.dumps('Invalid state parameter.'), 401)
        response.headers['Content-Type'] = 'application/json'
        return response
    # Obtain authorization code
    code = request.data

    try:
        # Upgrade the authorization code into a credentials object
        oauth_flow = flow_from_clientsecrets('client_secrets.json', scope='')
        oauth_flow.redirect_uri = 'postmessage'
        credentials = oauth_flow.step2_exchange(code)
    except FlowExchangeError:
        response = make_response(
            json.dumps('Failed to upgrade the authorization code.'), 401)
        response.headers['Content-Type'] = 'application/json'
        return response

    # Check that the access token is valid.
    access_token = credentials.access_token
    url = ('https://www.googleapis.com/oauth2/v1/tokeninfo?access_token=%s'
           % access_token)
    h = httplib2.Http()
    result = json.loads(h.request(url, 'GET')[1])
    # If there was an error in the access token info, abort.
    if result.get('error') is not None:
        response = make_response(json.dumps(result.get('error')), 500)
        response.headers['Content-Type'] = 'application/json'
        return response

    # Verify that the access token is used for the intended user.
    gplus_id = credentials.id_token['sub']
    if result['user_id'] != gplus_id:
        response = make_response(
            json.dumps("Token's user ID doesn't match given user ID."), 401)
        response.headers['Content-Type'] = 'application/json'
        return response

    # Verify that the access token is valid for this app.
    if result['issued_to'] != CLIENT_ID:
        response = make_response(
            json.dumps("Token's client ID does not match app's."), 401)
        print "Token's client ID does not match app's."
        response.headers['Content-Type'] = 'application/json'
        return response

    stored_credentials = login_session.get('credentials')
    stored_gplus_id = login_session.get('gplus_id')
    if stored_credentials is not None and gplus_id == stored_gplus_id:
        response = make_response(json.dumps('Current user is already '
                                            'connected.'), 200)
        response.headers['Content-Type'] = 'application/json'
        return response

    # Store the access token in the session for later use.
    login_session['access_token'] = credentials.access_token
    login_session['gplus_id'] = gplus_id

    # Get user info
    userinfo_url = "https://www.googleapis.com/oauth2/v1/userinfo"
    params = {'access_token': credentials.access_token, 'alt': 'json'}
    answer = requests.get(userinfo_url, params=params)

    data = answer.json()
    print(data)
    login_session['username'] = data['name']
    login_session['picture'] = data['picture']
    login_session['email'] = data['email']
    # ADD PROVIDER TO LOGIN SESSION
    login_session['provider'] = 'google'

    # see if user exists, if it doesn't make a new one
    user_id = getUserID(data["email"])
    if not user_id:
        user_id = createUser(login_session)
    login_session['user_id'] = user_id

    output = ''
    output += '<b>Welcome, '
    output += login_session['email']
    output += '!</b><br>'
    output += '<img src="'
    output += login_session['picture']
    output += ' " style = "width: 150px; text-align:center; ' \
        'height: 150px;border-radius: 150px; ' \
        '-webkit-border-radius: 150px;-moz-border-radius: 150px;"> '
    flash("You are now logged in as %s" % login_session['email'])
    print "done!"
    return output


def createUser(login_session):
    ''' Create new user '''
    newUser = User(name=login_session['username'],
                   email=login_session['email'],
                   picture=login_session['picture'])
    session.add(newUser)
    session.commit()
    user = session.query(User).filter_by(email=login_session['email']).one()
    return user.id


def getUserInfo(user_id):
    ''' Get user information '''
    user = session.query(User).filter_by(id=user_id).one()
    return user


def getUserID(email):
    ''' Get user id '''
    try:
        user = session.query(User).filter_by(email=email).one()
        return user.id
    except:
        return None


@app.route('/gdisconnect')
def gdisconnect():
    ''' Revoke a current user's token and reset their login_session'''
    # Only disconnect a connected user.
    credentials = login_session.get('credentials')
    if credentials is None:
        response = make_response(
            json.dumps('Current user not connected.'), 401)
        response.headers['Content-Type'] = 'application/json'
        return response
    access_token = credentials.access_token
    url = 'https://accounts.google.com/o/oauth2/revoke?token=%s' % access_token
    h = httplib2.Http()
    result = h.request(url, 'GET')[0]
    if result['status'] != '200':
        # For whatever reason, the given token was invalid.
        response = make_response(
            json.dumps('Failed to revoke token for given user.'), 400)
        response.headers['Content-Type'] = 'application/json'
        return response


@app.route('/disconnect')
def disconnect():
    '''Disconnect based on provider'''
    if 'provider' in login_session:
        if login_session['provider'] == 'google':
            gdisconnect()
            del login_session['gplus_id']
            # del login_session['credentials']
        del login_session['username']
        del login_session['email']
        del login_session['picture']
        del login_session['user_id']
        del login_session['provider']
        flash("You have successfully been logged out.")
        return redirect(url_for('showCatalog'))
    else:
        flash("You were not logged in")
        return redirect(url_for('showCatalog'))


########################################
# JSON APIs
########################################

@app.route('/catalog.json')
def catalogJSON():
    categories = session.query(Category).all()
    return jsonify(Category=[i.serializeWithProducts for i in categories])


@app.route('/categories.json')
def categoriesJSON():
    categories = session.query(Category).all()
    return jsonify(Category=[i.serialize for i in categories])


@app.route('/products.json')
def productsJSON():
    products = session.query(Product).all()
    return jsonify(Product=[i.serialize for i in products])


@app.route('/category/<category_name>/items.json')
def categoryItemsJSON(category_name):
    category = session.query(Category).filter_by(name=category_name).one()
    items = session.query(Product).filter_by(
        category_id=category.id).all()
    return jsonify(Product=[i.serialize for i in items])


@app.route('/category/<category_name>/details.json')
def categoryJSON(category_name):
    category = session.query(Category).filter_by(name=category_name).one()
    return jsonify(Category=category.serialize)


@app.route('/product/<product_name>/details.json')
def productJSON(product_name):
    product = session.query(Product).filter_by(name=product_name).one()
    return jsonify(Product=product.serialize)


########################################
# CATEGORIES
########################################

@app.route('/')
@app.route('/catalog')
def showCatalog():
    ''' Display latest items in catalog '''
    current_category = 'Latest Items'
    categories = session.query(Category).order_by(Category.name)
    products = session.query(Product).order_by(Product.id.desc()).all()
    return render_template('category/list.html',
                           categories=categories,
                           products=products,
                           current_category=current_category)


@app.route('/catalog/all')
def showCatalogAll():
    ''' Display all categories and all of their products'''
    current_category = 'All'
    categories = session.query(Category).order_by(asc(Category.name))
    products = session.query(Product).order_by(
        Product.category_id, Product.name).all()
    return render_template('category/list.html',
                           categories=categories,
                           products=products,
                           current_category=current_category)


@app.route('/catalog/<category_name>/items', methods=['GET', 'POST'])
def showCategory(category_name):
    '''Display specific category and their products'''
    current_category = category_name
    category = session.query(Category).filter_by(name=category_name).first()
    categories = session.query(Category).order_by(asc(Category.name)).all()
    products = session.query(Product).filter_by(
        category_id=category.id, status=1).order_by(asc(Product.name)).all()
    inactive_products = session.query(Product).filter_by(
        category_id=category.id, status=0).order_by(asc(Product.name)).all()
    # Determine if logged in user is category owner
    if 'username' in login_session and \
            category.user_id == login_session['user_id']:
        user_can_edit = 1
    else:
        user_can_edit = 0

    return render_template('category/details.html',
                           category=category,
                           categories=categories,
                           products=products,
                           current_category=current_category,
                           user_can_edit=user_can_edit,
                           inactive_products=inactive_products)


@app.route('/catalog/new/', methods=['GET', 'POST'])
def newCategory():
    '''Create a new category'''
    # determine if user logged in
    if 'username' not in login_session:
        return redirect('/login')
    if request.method == 'POST':
        if isUniqueSkuCode(request.form['sku_code']):
            newCategory = Category(name=request.form['name'],
                                   sku_code=request.form['sku_code'],
                                   user_id=login_session['user_id'])
            session.add(newCategory)
            flash(
                Markup('New category <b>{0}</b> successfully created'
                       .format(newCategory.name)))
            session.commit()
            return redirect(url_for('showCatalog'))
        else:
            flash('SKU code must be unique', 'danger')
    return render_template('category/new.html', category=None)


@app.route('/category/<category_name>/edit', methods=['GET', 'POST'])
def editCategory(category_name):
    '''Edit category'''
    category = session.query(Category).filter_by(name=category_name).first()
    # Determine if user logged in
    if 'username' not in login_session:
        return redirect('/login')
    # Determine if logged in user is product owner
    if category.user_id == login_session['user_id']:

        if request.method == 'POST':
            # if new sku code entered
            if category.sku_code != request.form['sku_code']:
                # Make sure SKU Code entered is unique
                if isUniqueSkuCode(request.form['sku_code']):
                    sku_code = request.form['sku_code']
                else:
                    flash('SKU code must be unique', 'danger')
                    return render_template('category/edit.html',
                                           category=category)
            category = Category(name=request.form['name'],
                                sku_code=request.form['sku_code'],
                                user_id=login_session['user_id'])
            session.add(category)
            flash(Markup('New category <b>{0}</b> successfully created'
                         .format(category.name)))
            session.commit()
            return redirect(url_for('showCatalog'))
    else:
        flash("You do not have permission to edit this category.", "danger")
        return redirect(url_for('showCategory', category_name=category.name))
    return render_template('category/edit.html', category=category)


@app.route('/category/<category_name>/delete', methods=['GET', 'POST'])
def deleteCategory(category_name):
    '''Delete category'''
    category = session.query(Category).filter_by(name=category_name).first()
    products = session.query(Product).filter_by(category_id=category.id).all()
    # Determine if logged in
    if 'username' not in login_session:
        return redirect('/login')
    # determine if uesr is category owner
    if category.user_id == login_session['user_id']:
        if request.method == 'POST':
            if products:
                # delete all associated products
                for product in products:
                    # delete photos
                    deleteProductPhotos(product.id)
                    # delete product
                    session.delete(product)
                    session.commit()
            # delete category
            session.delete(category)
            flash(Markup('<b>{0}</b> successfully deleted'
                         .format(category.name)))
            session.commit()
            return redirect(url_for('showCatalog'))
        return render_template('category/delete.html', category=category)
    else:
        flash("You do not have permission to delete this category.", "danger")
        return redirect(url_for('showCatalog'))


########################################
# PRODUCTS
########################################

@app.route('/catalog/<category_name>/<product_name>', methods=['GET', 'POST'])
def showProduct(category_name, product_name):
    '''Display product'''
    category = session.query(Category).filter_by(name=category_name).first()
    product = session.query(Product).filter_by(
        name=product_name, category_id=category.id).first()
    productPhoto = session.query(ProductPhoto).filter_by(
        product_id=product.id).first()
    # Determine if logged in user is product owner
    if 'username' in login_session and \
            product.user_id == login_session['user_id']:
        user_can_edit = 1
    else:
        user_can_edit = 0

    return render_template('product/detail.html',
                           product=product,
                           category=category,
                           user_can_edit=user_can_edit,
                           productPhoto=productPhoto)


@app.route('/catalog/<category_name>/new', methods=['GET', 'POST'])
def newProduct(category_name):
    '''Create a new product'''
    categories = session.query(Category).order_by(Category.name).all()
    preselected_category = session.query(
        Category).filter_by(name=category_name).first()
    # determine if user is logged in
    if 'username' not in login_session:
        return redirect('/login')
    if request.method == 'POST':
        category_id = request.form['category_id']

        # check to see if sku was left blank, auto-populate if empty
        if request.form['sku']:
            if isUniqueSku(request.form['sku']):
                sku = request.form['sku']
            else:
                flash('SKU code must be unique', 'danger')
                return render_template('product/new.html',
                                       product=None,
                                       categories=categories)
        else:
            sku = getNextSku(category_id)

        # check if photo was uploaded
        photo_uploaded = False
        # TODO - allow user to upload multiple photo files
        if 'photo' in request.files:
            file = request.files['photo']
            if file.filename != '':
                if file and allowed_file(file.filename):
                    filename = secure_filename(file.filename)
                    file.save(os.path.join(
                        app.config['UPLOAD_FOLDER'], filename))
                    photo_uploaded = True

        # create new product
        newProduct = Product(name=request.form['name'],
                             sku=sku,
                             price=request.form['price'],
                             status=request.form['status'],
                             category_id=category_id,
                             description=request.form['description'],
                             user_id=login_session['user_id'])
        session.add(newProduct)

        # save photo in database
        if photo_uploaded:
            newPhoto = ProductPhoto(filename=filename,
                                    order_placement=1,
                                    product=newProduct)
            session.add(newPhoto)

        flash(Markup('New product <b>{0}</b> successfully created'
                     .format(newProduct.name)))
        session.commit()
        return redirect(url_for('showCatalog'))
    else:
        return render_template('product/new.html',
                               product=None,
                               categories=categories,
                               preselected_category=preselected_category)


@app.route('/catalog/<product_name>/edit', methods=['GET', 'POST'])
def editProduct(product_name):
    '''Edit product'''
    product = session.query(Product).filter_by(name=product_name).first()
    category = session.query(Category) \
                      .filter_by(name=product.category.name) \
                      .first()
    categories = session.query(Category).order_by(Category.name).all()
    preselected_category = category
    # Determine if logged in
    if 'username' not in login_session:
        return redirect('/login')
    # Determine if logged in user is product owner
    if product.user_id == login_session['user_id']:
        # get form data
        if request.method == 'POST':

            # check if photo was uploaded
            photo_uploaded = False
            if 'photo' in request.files:
                file = request.files['photo']
                if file.filename != '':
                    if file and allowed_file(file.filename):
                        filename = secure_filename(file.filename)
                        file.save(os.path.join(
                            app.config['UPLOAD_FOLDER'], filename))
                        photo_uploaded = True

            # check to see if sku was changed
            if request.form['sku'] != product.sku:
                if isUniqueSku(request.form['sku']):
                    product.sku = request.form['sku']
                else:
                    flash('SKU code must be unique', 'danger')
                    return render_template('product/edit.html',
                                           category=category,
                                           product=product,
                                           categories=categories)

            # update product
            product.name = request.form['name']
            product.price = request.form['price']
            product.status = request.form['status']
            product.category_id = request.form['category_id']
            product.description = request.form['description']

            session.add(product)

            # save photo in database
            if photo_uploaded:
                # first delete any previously saved photos
                deleteProductPhotos(product.id)
                # save new photo
                newPhoto = ProductPhoto(filename=filename,
                                        order_placement=1,
                                        product=product)
                session.add(newPhoto)

            flash(
                Markup('<b>{0}</b> successfully edited'.format(product.name))
            )
            session.commit()

            return redirect(url_for('showProduct',
                                    category_name=category.name,
                                    product_name=product.name))
        return render_template('product/edit.html',
                               category=category,
                               product=product,
                               categories=categories,
                               preselected_category=preselected_category)
    else:
        flash("You do not have permission to edit this product.", "danger")
        return redirect(url_for('showCatalog'))


@app.route('/catalog/<product_name>/delete', methods=['GET', 'POST'])
def deleteProduct(product_name):
    '''Delete product'''
    product = session.query(Product).filter_by(name=product_name).first()
    category = session.query(Category).filter_by(
        name=product.category.name).first()
    # Determine if logged in
    if 'username' not in login_session:
        return redirect('/login')
    # Determine if logged in user is product owner
    if product.user_id == login_session['user_id']:
        if request.method == 'POST':
            # delete product
            deleteProductPhotos(product.id)
            session.delete(product)
            flash(Markup('<b>{0}</b> successfully deleted'
                         .format(product.name)))
            session.commit()
            return redirect(url_for('showCategory',
                                    category_name=category.name))
        return render_template('product/delete.html',
                               category=category,
                               product=product)
    else:
        flash("You do not have permission to delete this product.", "danger")
        return redirect(url_for('showCatalog'))


def deleteProductPhotos(product_id):
    ''' Find all photos for each product and delete the files '''
    photos = session.query(ProductPhoto).filter_by(product_id=product_id).all()
    for photo in photos:
        # delete file
        os.remove(os.path.join(app.config['UPLOAD_FOLDER'], photo.filename))
        # delete database entry
        session.delete(photo)
        session.commit()
    return True

########################################
# SKU FUNCTIONS
########################################


def getNextSku(category_id):
    ''' Return the next available sku number for the given category'''
    # get sku code from category
    category = session.query(Category).filter_by(id=category_id).first()
    # find the highest sku code for products in that category
    product_last_sku = session.query(Product).filter_by(
        category_id=category_id).order_by(Product.sku.desc()).first()
    if product_last_sku:
        # parse product sku number
        try:
            last_sku_code, last_sku_number = product_last_sku.sku.split("-")
            print(last_sku_number)
        except:
            last_sku_number = product_last_sku.sku
        # increment sku number
        product_next_sku = int(last_sku_number) + 1
    else:
        # default sku to 0001
        product_next_sku = '1'
    # concatenate sku code and sku number
    sku = str(category.sku_code) + "-" + str(product_next_sku)
    return sku


def isUniqueSkuCode(sku_code):
    '''Determine if category SKU code is unique'''
    sku_exists = session.query(Category).filter_by(sku_code=sku_code).first()
    if sku_exists:
        return False
    return True


def isUniqueSku(sku):
    '''Determine if product SKU is unique'''
    sku_exists = session.query(Product).filter_by(sku=sku).first()
    if sku_exists:
        return False
    return True


if __name__ == '__main__':
    app.secret_key = 'super_secret_key'
    app.debug = True
    app.run(host='0.0.0.0', port=8000)
