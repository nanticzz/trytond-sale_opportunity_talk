# This file is part of the sale_opportunity_talk module for Tryton.
# The COPYRIGHT file at the top level of this repository contains
# the full copyright notices and license terms.
from trytond.model import ModelView, ModelSQL, fields
from trytond.pool import Pool, PoolMeta
from trytond.transaction import Transaction
from trytond.sendmail import SMTPDataManager, sendmail_transactional
from datetime import datetime
from email.header import Header
from email.mime.text import MIMEText
from email.utils import parseaddr, make_msgid
import logging

logger = logging.getLogger(__name__)

CHECK_EMAIL = False
try:
    import emailvalid
    CHECK_EMAIL = True
except ImportError:
    logger.error('Unable to import emailvalid. Install emailvalid package.')

__all__ = [
    'SaleOpportunityTalk', 'SaleOpportunity']


class SaleOpportunityTalk(ModelSQL, ModelView):
    'Sale Opportunity Talk'
    __name__ = 'sale.opportunity.talk'
    _rec_name = 'display_text'
    date = fields.DateTime('Date', readonly=True)
    email = fields.Char('email')
    opportunity = fields.Many2One('sale.opportunity', 'Talks',
        required=True, ondelete='CASCADE')
    message = fields.Text('Comment')
    display_text = fields.Function(fields.Text('Display Text'),
        'get_display_text')
    unread = fields.Boolean('Unread')

    @classmethod
    def __setup__(cls):
        super(SaleOpportunityTalk, cls).__setup__()
        cls._order = [
            ('id', 'DESC'),
            ]

    @staticmethod
    def truncate_data(data):
        data_list = data and data.split('\n') or []
        if len(data_list) > 6:
            res = '\n\t'.join(data_list[:6]) + '...'
        else:
            res = '\n\t'.join(data_list)
        return res

    def get_display_text(self, name=None):
        display = ('(' + str(self.date) + '):\n' +
            self.truncate_data(self.message))
        if self.email:
            display = self.email + ' ' + display
        return display


class SaleOpportunity(metaclass=PoolMeta):
    __name__ = "sale.opportunity"
    talks = fields.One2Many('sale.opportunity.talk', 'opportunity', 'Talks')
    message = fields.Text('Comment')
    email_from = fields.Char('Email')
    email_cc = fields.Char('CC')
    message_id = fields.Char('Message ID')
    phone = fields.Char('Phone')

    @classmethod
    def __setup__(cls):
        super(SaleOpportunity, cls).__setup__()
        cls._order.insert(1, ('id', 'DESC'))
        cls._buttons.update({
            'add_reply': {},
            'talk_note': {},
            'talk_email': {},
            })

    @classmethod
    def search_rec_name(cls, name, clause):
        domain = super(SaleOpportunity, cls).search_rec_name(name, clause)
        if clause[1].startswith('!') or clause[1].startswith('not '):
            bool_op = 'AND'
        else:
            bool_op = 'OR'
        return [bool_op,
            domain,
            ('reference',) + tuple(clause[1:]),
            ('description',) + tuple(clause[1:]),
            ('party',) + tuple(clause[1:]),
            ('email_from',) + tuple(clause[1:]),
            ('email_cc',) + tuple(clause[1:]),
            ]

    @classmethod
    def _talk(self, opportunities):
        pool = Pool()
        Talk = pool.get('sale.opportunity.talk')
        User = pool.get('res.user')
        user = User(Transaction().user)

        reads = []
        for opportunity in opportunities:
            if not opportunity.message:
                self.raise_user_error('no_description')
            talk = Talk()
            talk.date = datetime.now()
            talk.email = user.email or None
            talk.opportunity = opportunity
            talk.message = opportunity.message
            talk.unread = False
            talk.save()

            for talk in opportunity.talks:
                if talk.unread:
                    reads.append(talk)

        if reads:
            Talk.write(reads, {'unread': False})

    @classmethod
    @ModelView.button
    def add_reply(self, opportunities):
        for opportunity in opportunities:
            if opportunity.talks:
                message = opportunity.talks[0].message
                self.write([opportunity], {
                    'message': '> ' + message.replace('\n', '\n> '),
                    })

    @classmethod
    @ModelView.button
    def talk_email(self, opportunities):
        SMTP = Pool().get('smtp.server')

        for opportunity in opportunities:
            if not opportunity.email_from:
                self.raise_user_error('no_email_from')
            if not opportunity.message:
                self.raise_user_error('no_description')

        server = SMTP.get_smtp_server_from_model('sale.opportunity')
        self.send_email(opportunities, server)  # Send email

        self._talk(opportunities)
        self.write(opportunities, {'message': None})

    @classmethod
    @ModelView.button
    def talk_note(self, opportunities):
        self._talk(opportunities)
        self.write(opportunities, {'message': None})

    @fields.depends('party', 'email_from')
    def on_change_party(self):
        super(SaleOpportunity, self).on_change_party()

        self.address = None
        if self.party:
            for address in self.party.addresses:
                self.address = address
                if address.email:
                    self.email_from = address.email
                break
            if not self.email_from:
                for contact in self.party.contact_mechanisms:
                    if contact.type == 'email':
                        self.email_from = contact.email
                        break

    @classmethod
    def send_email(self, opportunities, server):
        User = Pool().get('res.user')
        user = User(Transaction().user)

        from_ = user.email or server.smtp_email
        if server.smtp_use_email:
            from_ = server.smtp_email

        for opportunity in opportunities:
            if not opportunity.email_from:
                self.raise_user_error('no_email_from')
            if not opportunity.description:
                self.raise_user_error('no_description')

            recipients = []
            emails = opportunity.email_from.replace(' ', '').replace(',', ';')
            emails = emails.split(';')
            recipients = recipients + emails

            cc_addresses = []
            if opportunity.email_cc:
                emails = (opportunity.email_cc.replace(' ', '').replace(
                    ',', ';'))
                emails = emails.split(';')
                cc_addresses = cc_addresses + emails

            if CHECK_EMAIL:
                for recipient in recipients:
                    if not emailvalid.check_email(recipient):
                        self.raise_user_error('no_from_valid')
                if cc_addresses:
                    for cc_address in cc_addresses:
                        if not emailvalid.check_email(cc_address):
                            self.raise_user_error('no_recepients_valid')

            msg = MIMEText(opportunity.message, _charset='utf-8')
            msg['Subject'] = Header(opportunity.description, 'utf-8')
            msg['From'] = from_
            msg['To'] = ', '.join(recipients)
            msg['Cc'] = ', '.join(cc_addresses) if cc_addresses else None
            msg['Reply-to'] = server.smtp_email
            msg['Message-ID'] = make_msgid()

            if opportunity.message_id:
                msg["In-Reply-To"] = opportunity.message_id

            datamanager = SMTPDataManager()
            datamanager._server = server.get_smtp_server()
            sendmail_transactional(from_, recipients, msg, datamanager=datamanager)

            if not opportunity.message_id:
                self.write([opportunity], {
                        'message_id': msg.get('Message-ID'),
                        })

    @classmethod
    def getmail(self, server, messages):
        '''Get messages and load in opportunity talks'''
        pool = Pool()
        GetMail = pool.get('getmail.server')
        SaleOpportunity = pool.get('sale.opportunity')
        SaleOpportunityTalk = pool.get('sale.opportunity.talk')
        Attachment = pool.get('ir.attachment')

        for message in messages:
            msgeid = str(message.uid)
            msgfrom = parseaddr(message.from_addr)[1] if message.from_addr else None
            msgcc = message.cc if not message.cc == 'None' else None
            msgreferences = message.references
            msginrepplyto = getattr(message, "inrepplyto", None)
            msgsubject = message.title or 'Not subject'
            msgdate = message.date
            msgbody = message.body

            logger.info('Process email: %s' % (msgeid))

            #Search helpdesk by msg reference, msg in reply to or "description + email from"
            opportunity = None
            if msgreferences or msginrepplyto:
                references = msgreferences or msginrepplyto
                if '\r\n' in references:
                    references = references.split('\r\n')
                else:
                    references = references.split(' ')
                for ref in references:
                    ref = ref.strip()
                    opportunities = self.search([('message_id', '=', ref)], limit=1)
                    if opportunities:
                        opportunity = opportunities[0]
                        break
            if not opportunity:
                opportunities = self.search([
                    ('name', 'ilike', msgsubject),
                    ['OR',
                        ('email_from', '=', msgfrom),
                        ('email_cc', 'ilike', msgfrom),
                        ],
                    ])
                if opportunities:
                    opportunity = opportunities[0]

            # Create a new sale opportunity
            if not opportunity:
                party, address = GetMail.get_party_from_email(msgfrom)

                opportunity = SaleOpportunity()
                opportunity.description = msgsubject
                #~ oppotunity.start_date = GetMail.get_date(msgdate)
                opportunity.email_from = msgfrom
                opportunity.email_cc = msgcc
                opportunity.party = party if party else None
                opportunity.address = address if address else None
                opportunity.message_id = msgeid
                opportunity.save()

            # Create a new project_opportunity talk
            opportunity_talk = SaleOpportunityTalk()
            opportunity_talk.date = GetMail.get_date(msgdate)
            opportunity_talk.email = msgfrom
            opportunity_talk.opportunity = opportunity
            opportunity_talk.message  = msgbody
            opportunity_talk.unread = True
            opportunity_talk.save()

            # Create a attachments
            if server.attachment:
                for attachment in message.attachments:
                    attach = Attachment()
                    attach.name = attachment[0]
                    attach.type = 'data'
                    attach.data  = attachment[1]
                    attach.resource  = '%s' % (opportunity)
                    attach.save()
