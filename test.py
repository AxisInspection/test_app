
def prepare_invoice(invoice, progressive_number):
    # set company information
    company = frappe.get_doc("Company", invoice.company)
    # load_tax_itemised = update_itemised_tax_data()
    invoice.progressive_number = progressive_number
    invoice.unamended_name = get_unamended_name(invoice)
    invoice.company_data = company
    company_address = frappe.get_doc("Address", invoice.company_address)
    invoice.company_address_data = company_address

    # Set invoice type
    # if not invoice.type_of_document:
    #     if invoice.is_return and invoice.return_against:
    #         invoice.type_of_document = "TD04"  # Credit Note (Nota di Credito)
    #         invoice.return_against_unamended = get_unamended_name(
    #             frappe.get_doc("Sales Invoice", invoice.return_against)
    #         )
    #     else:
    #         invoice.type_of_document = "TD01"  # Sales Invoice (Fattura)

    # set customer information
    invoice.customer_data = frappe.get_doc("Customer", invoice.customer)
    customer_address = frappe.get_doc("Address", invoice.customer_address)
    
    invoice.customer_address_data = customer_address

    
    if invoice.shipping_address_name:
        invoice.shipping_address_data = frappe.get_doc(
            "Address", invoice.shipping_address_name)

    # if invoice.customer_data.is_public_administration:
    #     invoice.transmission_format_code = "FPA12"
    # else:
    #     invoice.transmission_format_code = "FPR12"

    invoice.e_invoice_items = [item for item in invoice.items]
    tax_data = get_invoice_summary(invoice.e_invoice_items, invoice.taxes)
    invoice.tax_data = tax_data

    # Check if stamp duty (Bollo) of 2 EUR exists.
    stamp_duty_charge_row = next(
        (tax for tax in invoice.taxes if tax.charge_type ==
         "Actual" and tax.tax_amount == 2.0), None
    )
    if stamp_duty_charge_row:
        invoice.stamp_duty = stamp_duty_charge_row.tax_amount

    for item in invoice.e_invoice_items:
        if item.tax_rate == 0.0 and item.tax_amount == 0.0 and tax_data.get("0.0"):
            item.tax_exemption_reason = tax_data["0.0"]["tax_exemption_reason"]

    customer_po_data = {}
    
    if invoice.po_no and invoice.po_date and invoice.po_no not in customer_po_data:
        customer_po_data[invoice.po_no] = invoice.po_date

    invoice.customer_po_data = customer_po_data
    seller_name = frappe.db.get_value("Company", invoice.company, "name")
    tax_id = frappe.db.get_value("Company", invoice.company, "tax_id")
    posting_date = getdate(invoice.posting_date)
    time = get_time(invoice.posting_time)
    seconds = time.hour * 60 * 60 + time.minute * 60 + time.second
    time_stamp = add_to_date(posting_date, seconds=seconds)
    time_stamp = time_stamp.strftime("%Y-%m-%dT%H:%M:%SZ")
    invoice_amount = str(invoice.grand_total)
    vat_amount = str(get_vat_amount(invoice))


    fatoora_obj = Fatoora(
    seller_name=seller_name,
    tax_number=tax_id, 
    invoice_date=time_stamp, 
    total_amount=invoice_amount, 
    tax_amount= vat_amount,
)
    

    invoice.qr_code =fatoora_obj.base64
    invoice.uuid = uuid.uuid1()
    try : 
        settings = frappe.db.get_list('Hash' , pluck='name')
        settings = frappe.get_doc('Hash' , settings[0])
        invoice.pih = settings.pih
    except : pass
    return invoice



@frappe.whitelist(allow_guest=True)
def generate_sign(doc, method):
    log_data('Generate hash started')
    cwd = os.getcwd()
    
    attachments = frappe.get_all(
        "File",
        fields=("name", "file_name", "attached_to_name", "file_url"),
        filters={"attached_to_name": ("in", doc.name), "attached_to_doctype": "Sales Invoice"},
    )
   
    for attachment in attachments:
        if attachment.file_name and attachment.file_name.endswith(".xml"):
            xml_filename = attachment.file_name
            file_url = attachment.file_url
          
    cwd = os.getcwd()
    signedxml = "Signed" + xml_filename
    site = frappe.local.site
    xml_file = cwd + '/' + site + '/public' + file_url

    log_data(f'Attachment data : {attachments}')
    
    # Load the XML content
    with open(xml_file, 'r', encoding='utf-8') as file:
        xml_content = file.read()

    # Parse the XML document
    xml_doc = etree.fromstring(xml_content.encode('utf-8'))

    # Generate a random RSA private key
    private_key = rsa.generate_private_key(
        public_exponent=65537,
        key_size=2048,
        backend=default_backend()
    )

    # Serialize the private key to PEM format
    pem_key = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.TraditionalOpenSSL,
        encryption_algorithm=serialization.NoEncryption()
    )

    # Sign the XML document
    signed_xml = sign_xml(xml_doc, private_key)

    # Serialize the signed XML document
    signed_xml_content = etree.tostring(signed_xml, encoding='utf-8', pretty_print=True)

    # Save the signed XML to a file
    signed_xml_path = cwd + '/' + site + '/public/files/' + signedxml
    with open(signed_xml_path, 'wb') as signed_file:
        signed_file.write(signed_xml_content)

    # Read the signed XML content and convert it to base64
    with open(signed_xml_path, 'rb') as signed_file:
        signed_xml_base64 = base64.b64encode(signed_file.read()).decode('utf-8')

    # Create a new File document for the signed XML
    signed_file_doc = frappe.get_doc({
        "doctype": "File",
        "file_name": signedxml,
        "attached_to_doctype": "Sales Invoice",
        "attached_to_name": doc.name,
        "content": signed_xml_base64,
    })

    # Save the signed File document
    signed_file_doc.insert()

    return signed_file_doc

def sign_xml(xml_doc, private_key):
    # Convert the XML document to bytes
    xml_bytes = etree.tostring(xml_doc, encoding='utf-8')

    # Generate the signature
    signature = private_key.sign(
        xml_bytes,
        padding.PKCS1v15(),
        SHA256()
    )

    # Convert the signature to base64
    signature_base64 = base64.b64encode(signature).decode('utf-8')

    # Find the ds:Signature element
    signature_elements = xml_doc.xpath("//ds:Signature", namespaces={"ds": "http://www.w3.org/2000/09/xmldsig#"})
    if not signature_elements:
        raise ValueError("ds:Signature element not found in the XML document.")

    # Find the SignatureValue element and set the signature value
    signature_value_elements = signature_elements[0].xpath(".//ds:SignatureValue", namespaces={"ds": "http://www.w3.org/2000/09/xmldsig#"})
    if not signature_value_elements:
        raise ValueError("SignatureValue element not found in the ds:Signature element.")

    signature_value_elements[0].text = signature_base64

    return xml_doc


